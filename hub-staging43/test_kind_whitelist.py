"""staging43 directed tests: F2 kind whitelist + D3 task_cancel noop audit."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import call, setup_members, TOKENS, DISP

FAIL = []
def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  {extra}" if extra and not cond else ""))
    if not cond: FAIL.append(name)

async def main():
    await setup_members()
    alice = TOKENS["alice"]

    # --- F2 whitelist ---
    ok, r = await call(alice, "room_post", room="pack-main", body="普通聊天", kind="chat")
    check("chat allowed", ok, str(r))
    for bad in ("report", "system", "task_card", "decision", "weird-kind", ""):
        ok, r = await call(alice, "room_post", room="pack-main", body="冒充", kind=bad)
        check(f"member kind={bad!r} rejected", not ok, str(r))
    ok, r = await call(DISP, "room_post", room="pack-main", body="调度员决议形态", kind="decision")
    check("dispatcher decision allowed", ok, str(r))
    ok, r = await call(DISP, "room_post", room="pack-main", body="冒充报告", kind="report")
    check("dispatcher report via room_post rejected", not ok, str(r))
    ok, r = await call(alice, "room_post", room="pack-main", body="任务#99999 请批准", kind="approval_request")
    check("approval_request reaches F1 gate", not ok and "existing task" in str(r), str(r))

    ok, t = await call(DISP, "task_create", room="pack-main", title="D3 取消审计", body="b", assignee_name="alice")
    tid = t["task_id"]
    ok, r = await call(DISP, "task_cancel", task_id=tid)
    check("first cancel works", ok and r.get("status") == "cancelled", str(r))
    ok, r = await call(DISP, "task_cancel", task_id=tid)
    check("second cancel returns cancelled", ok and r.get("status") == "cancelled", str(r))
    ok, a = await call(DISP, "task_audit", task_id=tid)
    actions = [e["action"] for e in a.get("events", [])]
    check("noop audit written", "task_cancel_noop" in actions, str(actions))
    check("real cancel audit kept", "task_cancel" in actions, str(actions))

    ok, t2 = await call(DISP, "task_create", room="pack-main", title="D3 done 取消", body="b", assignee_name="alice")
    tid2 = t2["task_id"]
    ok, c = await call(alice, "task_claim", task_id=tid2)
    tok = c["claim_token"]
    ok, r = await call(alice, "task_complete", task_id=tid2, claim_token=tok, result="done")
    check("complete to done", ok, str(r))
    await call(DISP, "task_review", task_id=tid2, approve=True)
    ok, r = await call(DISP, "task_cancel", task_id=tid2)
    check("cancel done returns done", ok and r.get("status") == "done", str(r))
    ok, a = await call(DISP, "task_audit", task_id=tid2)
    actions = [e["action"] for e in a.get("events", [])]
    check("done noop audit written", "task_cancel_noop" in actions, str(actions))

    print("RESULT:", "ALL PASS" if not FAIL else f"{len(FAIL)} FAILURES: {FAIL}")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
