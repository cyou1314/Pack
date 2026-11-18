import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import call, setup_members, TOKENS

PASS = FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ok {name}")
    else: FAIL += 1; print(f"  FAIL {name} {extra}")

async def main():
    await setup_members()
    A, D = TOKENS["alice"], TOKENS["dispatcher"]

    for i in range(3):
        await call(A, "room_post", room="pack-main", body=f"m{i}")
    ok, r = await call(A, "room_read_new", room="pack-main", since_id=0, limit=2)
    check("S1 limit=2 returns 2", ok and len(r["messages"]) == 2, str(r)[:80])
    ok, r = await call(A, "room_read_new", room="pack-main", since_id=0, limit=0)
    check("S1 limit=0 clamped to 1", ok and len(r["messages"]) == 1, str(r)[:80])
    ok, r = await call(A, "room_read_new", room="pack-main", since_id=0, limit=100000)
    check("S1 huge limit ok and bounded", ok and len(r["messages"]) == 3, str(len(r.get("messages", []))))

    ok, r = await call(D, "member_register", name="longprof", profile="x" * 2001)
    check("S3 profile 2001 rejected", not ok)
    ok, r = await call(D, "member_register", name="okprof", profile="x" * 2000)
    check("S3 profile 2000 accepted", ok, str(r)[:80])

    ok, r = await call(A, "room_post", room="pack-main", body="y" * 20001)
    check("F4 body 20001 rejected", not ok)
    ok, r = await call(A, "room_post", room="pack-main", body="y" * 20000)
    check("F4 body 20000 accepted", ok, str(r)[:80])
    mid = r["message_id"]
    ok, r = await call(A, "message_edit", message_id=mid, new_body="z" * 20001)
    check("F4 edit 20001 rejected", not ok)
    ok, r = await call(A, "message_edit", message_id=mid, new_body="short")
    check("F4 edit normal ok", ok, str(r)[:80])

    ok, t = await call(D, "task_create", room="pack-main", title="holder test", body="", assignee_name="alice")
    tid = t["task_id"]
    ok, c = await call(A, "task_claim", task_id=tid)
    check("F3 claim ok", ok and "claim_token" in c, str(c)[:80])
    ok, r = await call(A, "task_update", task_id=tid, claim_token="wrong-token", note="x")
    check("F3 wrong token rejected", not ok)
    ok, r = await call(A, "task_update", task_id=tid, claim_token=c["claim_token"], note="x")
    check("F3 right token ok", ok, str(r)[:80])

    print(f"\n{PASS} passed, {FAIL} failed")
    return FAIL

sys.exit(1 if asyncio.run(main()) else 0)
