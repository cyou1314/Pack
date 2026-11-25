#!/usr/bin/env python3
"""Exercise opt-in collaboration cancellation against the actual Hub schema and MCP."""
import asyncio
import os
import tempfile
import threading
import time

from test_task_lifecycle_guards import hub, free_port, wait_port, call
from collaboration_store import initialize, write_transaction, create_run, snapshot
from collaboration_dispatch import bind_task, reserve_claim
from collaboration_guard import install_task_guards
from collaboration_context import grant_context, revoke_context, register_executor, revoke_executor


def fixture(conn, room, owner, member, source, now, *, deadline, claimed=False, lease=None,
            create_card=True):
    with write_transaction(conn):
        run = create_run(conn, room_id=room, source_message_id=source, deadline=deadline,
                         now=now, actor_role="owner", mode="focus",
                         eligible={str(member)}, leader=str(member))
        if not create_card:
            return run, None
        task = conn.execute(
            "INSERT INTO tasks(room_id,title,body,created_by,assignee,status,created_at,updated_at)"
            " VALUES(?,?,?,?,?,'open',?,?)",
            (room, "collaboration fixture", "test input", owner, member, now, now)).lastrowid
        bind_task(conn, run, "answer", task_id=task, member_id=member, now=now)
        if claimed:
            assert reserve_claim(conn, task, member_id=member, eligible_ids={member}, now=now)
            conn.execute(
                "UPDATE tasks SET status='claimed',attempts=1,claim_token='fixture',lease_expires=? WHERE id=?",
                (lease, task))
            conn.execute(
                "INSERT INTO runs(task_id,member_id,fence,claim_token,status,started_at)"
                " VALUES(?,?,1,'fixture','running',?)", (task, member, now))
        return run, task


def assert_closed(conn, run, task, status):
    assert snapshot(conn, run)["status"] == status
    if task is not None:
        row = conn.execute("SELECT status,claim_token,lease_expires FROM tasks WHERE id=?",
                           (task,)).fetchone()
        assert tuple(row) == ("cancelled", None, None), tuple(row)
        assert not conn.execute(
            "SELECT 1 FROM runs WHERE task_id=? AND status='running'", (task,)).fetchone()


def main():
    with tempfile.TemporaryDirectory(prefix="pack-collab-hub-") as temp:
        hub.BASE = temp
        hub.DB_PATH = os.path.join(temp, "pack.db")
        hub.ATTACH_DIR = os.path.join(temp, "attachments")
        hub.init()
        with open(os.path.join(temp, ".dispatcher_token"), encoding="utf-8") as f:
            token = f.read().strip()
        conn = hub.db()
        try:
            assert not hub._collaboration_present(conn), "legacy schema must stay unchanged"
            initialize(conn)
            install_task_guards(conn)
            port = free_port()
            server = hub.build_server()
            threading.Thread(target=server.run, kwargs={
                "transport": "http", "host": "127.0.0.1", "port": port, "path": "/mcp"
            }, daemon=True).start()
            assert wait_port(port)

            def dispatch(name, args):
                return asyncio.run(call(port, token, name, args))

            reg = dispatch("member_register", {"name": "collab-fixture-member", "provider": "fixture-provider"})
            assert reg.get("token"), reg
            member = conn.execute("SELECT id FROM members WHERE name='collab-fixture-member'").fetchone()[0]
            owner = conn.execute("SELECT id FROM members WHERE role='dispatcher'").fetchone()[0]
            room = conn.execute("SELECT id FROM rooms WHERE name='pack-main'").fetchone()[0]
            now = time.time()
            run, task = fixture(conn, room, owner, member, 1, now,
                                deadline=now+600, claimed=True, lease=now+300)
            conn.execute("UPDATE desktop_lock SET holder=?,task_id=?,acquired_at=? WHERE id=1",
                         ("collab-fixture-member", task, now))
            conn.commit()
            result = dispatch("task_cancel", {"task_id": task})
            assert result.get("status") == "cancelled", result
            assert_closed(conn, run, task, "cancelled")
            assert not hub._desktop_snapshot(conn)["held"]
            assert dispatch("task_cancel", {"task_id": task}).get("status") == "cancelled"

            expired, expired_task = fixture(conn, room, owner, member, 2, now-100,
                                            deadline=now-10)
            failed, failed_task = fixture(conn, room, owner, member, 3, now-100,
                                          deadline=now+600, claimed=True, lease=now-10)
            legacy = dispatch("task_create", {"room": "pack-main", "title": "legacy",
                              "body": "test", "assignee_name": "collab-fixture-member"})
            legacy_id = legacy["task_id"]
            hub.sweep_once()
            assert_closed(conn, expired, expired_task, "expired")
            assert_closed(conn, failed, failed_task, "failed")
            assert conn.execute("SELECT status FROM tasks WHERE id=?", (legacy_id,)).fetchone()[0] == "open"

            # Exercise actual MCP claim, run fence, review and block paths.
            approved, approved_task = fixture(conn, room, owner, member, 6, now, deadline=now+600)
            def agent(name, args):
                return asyncio.run(call(port, reg["token"], name, args))
            claim = agent("task_claim", {"task_id": approved_task})
            assert claim.get("claim_token"), claim
            ct = claim["claim_token"]
            assert snapshot(conn, approved)["calls_used"] == 1
            assert "error" in agent("task_claim", {"task_id": approved_task})
            started = agent("run_start", {"task_id": approved_task, "claim_token": ct})
            assert started.get("fence") == 1, started
            assert "error" in agent("run_start", {"task_id": approved_task, "claim_token": ct})
            finished = agent("run_finish", {"task_id": approved_task, "claim_token": ct})
            assert finished.get("status") == "closed", finished
            assert "error" in agent("run_start", {"task_id": approved_task, "claim_token": ct})
            assert "error" in agent("task_transfer", {"task_id": approved_task,
                "claim_token": ct, "to_member": "other"})
            complete = agent("task_complete", {"task_id": approved_task, "claim_token": ct,
                "result": "fixture result"})
            assert complete.get("status") == "review", complete
            assert snapshot(conn, approved)["status"] == "active"
            review = dispatch("task_review", {"task_id": approved_task, "approve": True})
            assert review.get("status") == "done", review
            assert snapshot(conn, approved)["status"] == "succeeded"

            rejected, rejected_task = fixture(conn, room, owner, member, 7, now, deadline=now+600)
            claim = agent("task_claim", {"task_id": rejected_task})
            assert claim.get("claim_token"), claim
            complete = agent("task_complete", {"task_id": rejected_task,
                "claim_token": claim["claim_token"], "result": "fixture result"})
            assert complete.get("status") == "review", complete
            review = dispatch("task_review", {"task_id": rejected_task, "approve": False})
            assert review.get("status") == "cancelled", review
            assert_closed(conn, rejected, rejected_task, "failed")
            assert "error" in dispatch("task_release", {"task_id": rejected_task})

            blocked, blocked_task = fixture(conn, room, owner, member, 8, now, deadline=now+600)
            claim = agent("task_claim", {"task_id": blocked_task})
            assert claim.get("claim_token"), claim
            result = agent("task_block", {"task_id": blocked_task,
                "claim_token": claim["claim_token"], "reason": "fixture failure",
                "fail_class": "task"})
            assert result.get("status") == "cancelled", result
            assert_closed(conn, blocked, blocked_task, "failed")

            owner_reg = dispatch("member_register", {"name": "owner"})
            assert owner_reg.get("token"), owner_reg
            real_owner = conn.execute("SELECT id FROM members WHERE name='owner'").fetchone()[0]
            with write_transaction(conn):
                # Other lifecycle fixtures use synthetic source IDs 1..8.
                # Allocate a real message ID outside that reserved test range.
                message = conn.execute(
                    "SELECT MAX(1000,COALESCE(MAX(id),0))+1 FROM messages").fetchone()[0]
                conn.execute(
                    "INSERT INTO messages(id,room_id,author_id,kind,body,created_at) VALUES(?,?,?,'chat',?,?)",
                    (message,room,real_owner,"approved context fixture",now))
            context_run, context_task = fixture(conn,room,owner,member,message,now,deadline=now+600)
            with write_transaction(conn):
                assert grant_context(conn,context_run,"answer",actor_id=real_owner,
                    member_id=member,refs=[("owner_message",message)],now=now)
            claim = agent("task_claim",{"task_id":context_task})
            assert claim.get("claim_token"),claim
            ct = claim["claim_token"]
            response = agent("collaboration_context_get",{"task_id":context_task,"claim_token":ct})
            assert response.get("items",[{}])[0].get("text") == "approved context fixture",response
            assert "error" in agent("collaboration_context_get",{"task_id":context_task,"claim_token":"wrong"})
            with write_transaction(conn):
                conn.execute("UPDATE messages SET body='changed',edited_at=? WHERE id=?",(now+1,message))
            assert "error" in agent("collaboration_context_get",{"task_id":context_task,"claim_token":ct})
            with write_transaction(conn):
                assert revoke_context(conn,context_run,"answer",actor_id=real_owner)
            assert "error" in agent("collaboration_context_get",{"task_id":context_task,"claim_token":ct})
            dispatch("task_cancel",{"task_id":context_task})

            # An ordinary Agent must not read linked content through legacy APIs.
            other = dispatch("member_register", {"name": "collab-other"})
            assert other.get("token"), other
            def outsider(name,args):
                return asyncio.run(call(port,other["token"],name,args))
            private_run, private_task = fixture(conn,room,owner,member,10000,now,deadline=now+600)
            claim = agent("task_claim",{"task_id":private_task})
            assert claim.get("claim_token"),claim
            ct = claim["claim_token"]
            update = agent("task_update",{"task_id":private_task,"claim_token":ct,"note":"private-progress-marker"})
            assert update.get("status") == "working",update
            rejected_attachment = agent("task_complete",{"task_id":private_task,"claim_token":ct,
                "result":"private-result-marker","attachment_ids":"[1]"})
            assert "error" in rejected_attachment,rejected_attachment
            complete = agent("task_complete",{"task_id":private_task,"claim_token":ct,
                "result":"private-result-marker"})
            assert complete.get("status") == "review",complete
            for caller in (agent,outsider):
                listing=caller("task_list",{"room":"pack-main"})
                card=next(x for x in listing["tasks"] if x["id"]==private_task)
                assert card["collaboration_context_required"] is True,card
                assert card["result"] is None and card["envelope"] is None,card
                assert "test input" not in card["body"],card
                messages=caller("room_read_new",{"room":"pack-main","since_id":0,"limit":200})
                reports=[x for x in messages["messages"] if x["task_id"]==private_task]
                assert len(reports)==2,reports
                assert all(x["body"]=="[Collaboration content restricted]" for x in reports),reports
                inbox=caller("inbox_pull",{"room":"pack-main","since_id":0})
                assert "error" not in inbox,inbox
                assert "private-result-marker" not in str(inbox),inbox
                assert "private-progress-marker" not in str(inbox),inbox
            listing=dispatch("task_list",{"room":"pack-main"})
            card=next(x for x in listing["tasks"] if x["id"]==private_task)
            assert card["result"]=="private-result-marker",card
            assert "error" in outsider("egress_check",{"url":"https://example.com","task_id":private_task})
            assert dispatch("task_review",{"task_id":private_task,"approve":True}).get("status")=="done"

            # Dedicated executor credentials never grant access to general Hub tools.
            dedicated = dispatch("member_register", {"name":"collab-dedicated","provider":"fixture-api"})
            assert dedicated.get("token"),dedicated
            dm = dedicated["member_id"]
            account = "12345678-1234-4234-8234-123456789abc"
            def executor(name,args):
                return asyncio.run(call(port,dedicated["token"],name,args))
            with write_transaction(conn):
                assert register_executor(conn,actor_id=real_owner,member_id=dm,
                    provider="fixture-api",access_kind="api",account_ref=account)
                assert not register_executor(conn,actor_id=real_owner,member_id=dm,
                    provider="fixture-api",access_kind="api",account_ref=account)
            for tool,args in (
                ("room_read_new",{"room":"pack-main"}),("room_post",{"room":"pack-main","body":"no"}),
                ("inbox_pull",{"room":"pack-main"}),("memory_read",{"scope":"room-shared"}),
                ("attachment_list",{"room":"pack-main"}),("egress_check",{"url":"https://example.com"}),
                ("desktop_acquire",{"task_id":private_task,"claim_token":"no"})):
                assert "error" in executor(tool,args),(tool,args)
            assert executor("collaboration_executor_info",{})=={
                "provider":"fixture-api","access_kind":"api","account_ref":account}
            assert "error" in outsider("collaboration_executor_info",{})
            assert executor("heartbeat",{}).get("ok") is True
            assert executor("task_list",{}).get("tasks")==[]
            assert "error" in executor("task_claim",{"task_id":legacy_id})
            with write_transaction(conn):
                message2=conn.execute("INSERT INTO messages(room_id,author_id,kind,body,created_at)"
                    " VALUES(?,?,'chat',?,?)",(room,real_owner,"dedicated input",now)).lastrowid
            dr,dt=fixture(conn,room,owner,dm,message2,now,deadline=now+600)
            assert "error" in executor("task_claim",{"task_id":dt})
            assert snapshot(conn,dr)["calls_used"]==0
            with write_transaction(conn):
                assert grant_context(conn,dr,"answer",actor_id=real_owner,member_id=dm,
                    refs=[("owner_message",message2)],now=now)
            listing=executor("task_list",{})
            assert [x["id"] for x in listing["tasks"]]==[dt],listing
            c=executor("task_claim",{"task_id":dt})
            assert c.get("claim_token"),c
            token2=c["claim_token"]
            info=executor("collaboration_context_get",{"task_id":dt,"claim_token":token2})
            assert info.get("account_ref")==account and info.get("access_kind")=="api",info
            assert "error" in executor("task_complete",{"task_id":dt,"claim_token":token2,"result":"premature"})
            assert executor("run_start",{"task_id":dt,"claim_token":token2}).get("fence")==1
            assert executor("task_complete",{"task_id":dt,"claim_token":token2,"result":"dedicated result"}).get("status")=="review"
            assert dispatch("task_review",{"task_id":dt,"approve":True}).get("status")=="done"
            with write_transaction(conn):
                assert revoke_executor(conn,actor_id=real_owner,member_id=dm)
            assert "error" in executor("collaboration_executor_info",{})
            assert "error" in executor("heartbeat",{})
            assert "error" in executor("room_read_new",{"room":"pack-main"})
            assert "error" in executor("task_list",{})

            # Real owner creates a private managed compare run. No raw prompt
            # in the old room/inbox/task surfaces, no automatic legacy chat.
            def owner_call(name,args):
                return asyncio.run(call(port,owner_reg["token"],name,args))
            members=[]
            for ix in range(2):
                registered=dispatch("member_register",{"name":f"managed-executor-{ix}","provider":"openai"})
                assert registered.get("token"),registered
                binding={"member_id":registered["member_id"],"provider":"openai","access_kind":"api",
                         "account_ref":f"12345678-1234-4234-8234-123456789ab{ix}"}
                assert "error" in outsider("collaboration_executor_bind",binding)
                assert owner_call("collaboration_executor_bind",binding).get("created") is True
                members.append(registered)
            # Routing preview is read-only, quota unknown requires opt-in.
            for registered in members:
                assert asyncio.run(call(port,registered["token"],"heartbeat",{})).get("ok") is True
            preview=owner_call("collaboration_route_preview",{"mode":"compare","max_calls":3})
            assert preview.get("suggestion") is None,preview
            preview=owner_call("collaboration_route_preview",{"mode":"compare","max_calls":3,
                "allow_unknown_quota":True})
            assert preview.get("suggestion",{}).get("planned_calls")==3,preview
            assert "error" in outsider("collaboration_route_preview",{"mode":"focus"})
            request={"room":"pack-main","request_key":"f"*32,"body":"managed-private-root-marker",
                "mode":"compare","leader":members[0]["member_id"],"workers":[members[1]["member_id"]],
                "deadline":time.time()+600,"max_calls":3}
            assert "error" in outsider("collaboration_create",request)
            created=owner_call("collaboration_create",request)
            assert created.get("status")=="active",created
            managed=created["run_id"]
            assert owner_call("collaboration_create",request)==created
            assert "error" in owner_call("collaboration_create",{**request,"body":"different"})
            assert "error" in outsider("collaboration_status",{"run_id":managed})
            assert "managed-private-root-marker" not in str(outsider("room_read_new",{"room":"pack-main","limit":200}))
            assert "managed-private-root-marker" not in str(outsider("task_list",{}))
            assert conn.execute("SELECT COUNT(*) FROM collaboration_task_links WHERE run_id=?",(managed,)).fetchone()[0]==2
            steps={x["key"]:x for x in created["steps"]}
            assert steps["final"]["task_id"] is None
            busy_preview=owner_call("collaboration_route_preview",{"mode":"focus","allow_unknown_quota":True})
            assert busy_preview.get("suggestion") is None,busy_preview
            def finish_managed(step,registered,result):
                def execute(name,args):
                    return asyncio.run(call(port,registered["token"],name,args))
                tid=step["task_id"]
                claim=execute("task_claim",{"task_id":tid})
                assert claim.get("claim_token"),claim
                args={"task_id":tid,"claim_token":claim["claim_token"]}
                context=execute("collaboration_context_get",args)
                assert context.get("items",[{}])[0].get("text")==request["body"],context
                assert execute("run_start",args).get("fence")==1
                assert execute("task_complete",{**args,"result":result}).get("status")=="review"
                assert dispatch("task_review",{"task_id":tid,"approve":True}).get("status")=="done"
                return context
            finish_managed(steps["analysis-0"],members[0],"first result")
            assert owner_call("collaboration_status",{"run_id":managed})["steps"][-1]["task_id"] is None
            finish_managed(steps["analysis-1"],members[1],"second result")
            status=owner_call("collaboration_status",{"run_id":managed})
            final=next(s for s in status["steps"] if s["key"]=="final")
            assert final["task_id"],status
            context=finish_managed(final,members[0],"final result")
            assert context["role"]=="synthesis" and context["max_output_tokens"]==4096,context
            assert {i["text"] for i in context["items"] if i["kind"]=="dependency_result"}=={"first result","second result"}
            assert owner_call("collaboration_status",{"run_id":managed})["status"]=="succeeded"
            assert owner_call("collaboration_create",request)["status"]=="succeeded"
            invalid={**request,"request_key":"e"*32,"max_calls":2}
            assert "error" in owner_call("collaboration_create",invalid)
            assert conn.execute("SELECT COUNT(*) FROM collaboration_requests WHERE request_key=?",("e"*32,)).fetchone()[0]==0
            focus={**request,"request_key":"d"*32,"mode":"focus","workers":[],"max_calls":1}
            pending=owner_call("collaboration_create",focus)
            assert pending.get("status")=="active",pending
            assert owner_call("collaboration_cancel",{"run_id":pending["run_id"]})["status"]=="cancelled"
            assert owner_call("collaboration_cancel",{"run_id":pending["run_id"]})["status"]=="cancelled"

            removed, removed_task = fixture(conn, room, owner, member, 4, now, deadline=now+600)
            unbound, _ = fixture(conn, room, owner, member, 5, now, deadline=now+600, create_card=False)
            result = dispatch("member_remove", {"name": "collab-fixture-member"})
            assert result.get("removed") == "collab-fixture-member", result
            assert_closed(conn, removed, removed_task, "failed")
            assert_closed(conn, unbound, None, "failed")
            assert tuple(conn.execute("SELECT status,assignee FROM tasks WHERE id=?",
                                      (legacy_id,)).fetchone()) == ("open", None)
            print("PASS real Hub collaboration cancellation, expiry, member removal and legacy compatibility")
        finally:
            conn.close()


if __name__ == "__main__":
    main()
