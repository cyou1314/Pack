"""Actual MCP claim/context/run_start with opt-in Registry generation fences."""
import asyncio
import os
import tempfile
import threading
import time

from test_task_lifecycle_guards import hub, free_port, wait_port, call
from test_collaboration_hub import fixture
from collaboration_migrate import migrate
from collaboration_store import write_transaction, snapshot
from collaboration_context import register_executor, grant_context
from collaboration_connection_binding import bind_connection
from registry_offline_chain import apply_offline_chain
from registry_binding_migration import apply_binding_extension, apply_control_extension
from agent_execution_control import set_execution_state


def check_generation_parser():
    assert hub._registry_claim_generation("rg1.secret-_") == 1
    assert hub._registry_claim_generation("rg9223372036854775807.secret") == 9223372036854775807
    for invalid in ("rg0.secret", "rg01.secret", "rg1", "rg1.", "rg1Xsecret",
                    "rg1\\xsecret", "rg1.secret.extra", "rg1.secret\\n", "legacy-secret"):
        try:
            hub._registry_claim_generation(invalid)
        except PermissionError:
            continue
        raise AssertionError("invalid generation token was accepted")


def main():
    check_generation_parser()
    with tempfile.TemporaryDirectory(prefix="pack-registry-claim-") as temp:
        hub.BASE=temp
        hub.DB_PATH=os.path.join(temp,"pack.db")
        hub.ATTACH_DIR=os.path.join(temp,"attachments")
        hub.REGISTRY_EXECUTION_ENABLED=False
        hub.init()
        with open(os.path.join(temp,".dispatcher_token"),encoding="utf-8") as stream:
            token=stream.read().strip()
        migrate(hub.DB_PATH,enabled=True)
        conn=hub.db()
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            apply_offline_chain(conn,now=time.time())
            apply_binding_extension(conn,now=time.time())
            apply_control_extension(conn,now=time.time())
            port=free_port()
            server=hub.build_server()
            threading.Thread(target=server.run,kwargs={
                "transport":"http","host":"127.0.0.1","port":port,"path":"/mcp"
            },daemon=True).start()
            assert wait_port(port)
            def dispatch(name,args):
                return asyncio.run(call(port,token,name,args))
            owner=dispatch("member_register",{"name":"owner"})["member_id"]
            worker=dispatch("member_register",{"name":"registry-worker","provider":"chatgpt"})
            mid=worker["member_id"]
            account="12345678-1234-4234-8234-123456789abc"
            room=conn.execute("SELECT id FROM rooms WHERE name='pack-main'").fetchone()[0]
            now=time.time()
            def agent(name,args):
                return asyncio.run(call(port,worker["token"],name,args))
            with write_transaction(conn):
                register_executor(conn,actor_id=owner,member_id=mid,
                                  provider="chatgpt",access_kind="native",account_ref=account)
                conn.execute("INSERT INTO agents(agent_id,provider_id,legacy_member_id,display_name,status,created_at,updated_at) "
                             "VALUES('a','openai',?,'Registry fixture','active',1,1)",(mid,))
                conn.execute("INSERT INTO agent_connections(connection_id,agent_id,mode,auth_kind,status,created_at,updated_at) "
                             "VALUES('c','a','native_account','none','connected',1,1)")
                bind_connection(conn,actor_id=owner,member_id=mid,account_ref=account,agent_id="a",connection_id="c")
            def state(value):
                with write_transaction(conn):
                    return set_execution_state(conn,actor_id=owner,member_id=mid,state=value,now=time.time())
            def card():
                with write_transaction(conn):
                    msg=conn.execute("INSERT INTO messages(room_id,author_id,kind,body,created_at) "
                        "VALUES(?,?,'chat','authorized fixture',?)",(room,owner,now)).lastrowid
                run,task=fixture(conn,room,owner,mid,msg,now,deadline=now+600)
                with write_transaction(conn):
                    grant_context(conn,run,"answer",actor_id=owner,member_id=mid,
                                  refs=[("owner_message",msg)],now=now)
                return run,task
            run,task=card()
            assert "error" in agent("task_claim",{"task_id":task})
            state("enabled")
            assert "error" in agent("task_claim",{"task_id":task})
            assert snapshot(conn,run)["calls_used"]==0
            hub.REGISTRY_EXECUTION_ENABLED=True
            claim=agent("task_claim",{"task_id":task})
            ct=claim["claim_token"]
            assert ct.startswith("rg1."),claim
            args={"task_id":task,"claim_token":ct}
            assert "error" not in agent("collaboration_context_get",args)
            other_run,other_task=card()
            denied=agent("task_claim",{"task_id":other_task})
            assert denied=={"task_id":other_task,"status":"unavailable","code":"execution_account_busy"},denied
            assert snapshot(conn,other_run)["calls_used"]==0
            assert conn.execute("SELECT status FROM tasks WHERE id=?",(other_task,)).fetchone()[0]=="open"
            state("paused")
            assert "error" in agent("collaboration_context_get",args)
            assert "error" in agent("run_start",args)
            state("enabled")
            assert "error" in agent("run_start",args)
            assert "error" in agent("collaboration_context_get",args)
            assert conn.execute("SELECT COUNT(*) FROM runs WHERE task_id=?",(task,)).fetchone()[0]==0
            assert dispatch("task_cancel",{"task_id":task})["status"]=="cancelled"
            run2,task2=other_run,other_task
            claim2=agent("task_claim",{"task_id":task2})
            ct2=claim2["claim_token"]
            assert ct2.startswith("rg3."),claim2
            assert agent("run_start",{"task_id":task2,"claim_token":ct2})["fence"]==1
            state("paused")
            # An already-started task can still finish reporting through
            # existing completion paths; pause only blocks new execution.
            result=agent("task_complete",{"task_id":task2,"claim_token":ct2,"result":"fixture"})
            assert result.get("status")=="review",result
            assert snapshot(conn,run2)["calls_used"]==1
            # Fill the shared Hub capacity with distinct identities, including
            # both native and API modes. No actual Provider is invoked.
            active=[]
            for i in range(5):
                name=f"capacity-worker-{i}"
                provider,access,provider_id,mode,auth_kind=(
                    ("openai","api","openai","official_api","api_key") if i%2
                    else ("chatgpt","native","openai","native_account","none"))
                worker2=dispatch("member_register",{"name":name,"provider":provider})
                mid2=worker2["member_id"]
                account2=f"00000000-0000-4000-8000-{i+1:012d}"
                aid,cid=f"capacity-a{i}",f"capacity-c{i}"
                with write_transaction(conn):
                    register_executor(conn,actor_id=owner,member_id=mid2,
                                      provider=provider,access_kind=access,account_ref=account2)
                    conn.execute("INSERT INTO agents(agent_id,provider_id,legacy_member_id,display_name,status,created_at,updated_at) "
                                 "VALUES(?,?,?,?,'active',1,1)",(aid,provider_id,mid2,name))
                    conn.execute("INSERT INTO agent_connections(connection_id,agent_id,mode,auth_kind,status,created_at,updated_at) "
                                 "VALUES(?,?,?,?,'connected',1,1)",(cid,aid,mode,auth_kind))
                    bind_connection(conn,actor_id=owner,member_id=mid2,account_ref=account2,agent_id=aid,connection_id=cid)
                    set_execution_state(conn,actor_id=owner,member_id=mid2,state="enabled",now=time.time())
                    msg=conn.execute("INSERT INTO messages(room_id,author_id,kind,body,created_at) "
                                     "VALUES(?,?,'chat','capacity fixture',?)",(room,owner,now)).lastrowid
                rr,tt=fixture(conn,room,owner,mid2,msg,now,deadline=now+600)
                with write_transaction(conn):
                    grant_context(conn,rr,"answer",actor_id=owner,member_id=mid2,
                                  refs=[("owner_message",msg)],now=now)
                active.append((rr,tt,worker2["token"]))
            def claim_capacity(row):
                return asyncio.run(call(port,row[2],"task_claim",{"task_id":row[1]}))
            for row in active[:4]:
                assert claim_capacity(row)["status"]=="claimed"
            refused=claim_capacity(active[4])
            assert refused=={"task_id":active[4][1],"status":"unavailable","code":"execution_capacity"},refused
            assert snapshot(conn,active[4][0])["calls_used"]==0
            # A second independent database connection sees the same durable
            # capacity; no reliance on Node/Swift process-local counters.
            from registry_claim_capacity import admission_refusal
            separate=hub.db()
            try:
                separate.execute("BEGIN IMMEDIATE")
                assert admission_refusal(separate,mid2)=="execution_capacity"
            finally:
                separate.close()
            assert dispatch("task_cancel",{"task_id":active[0][1]})["status"]=="cancelled"
            assert claim_capacity(active[4])["status"]=="claimed"
            # Ordinary legacy member remains operational even when the
            # process opt-in is on.
            legacy=dispatch("member_register",{"name":"legacy-worker","provider":"muse"})
            task3=dispatch("task_create",{"room":"pack-main","title":"legacy","body":"fixture",
                                          "assignee_name":"legacy-worker"})["task_id"]
            old=asyncio.run(call(port,legacy["token"],"task_claim",{"task_id":task3}))
            assert old.get("claim_token") and not old["claim_token"].startswith("rg"),old
        finally:
            conn.close()


if __name__=="__main__":
    main()
