import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import call, setup_members, TOKENS

PASS = FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ok {name}")
    else: FAIL += 1; print(f"  FAIL {name} {extra}")

async def main():
    await setup_members()
    D = TOKENS["dispatcher"]
    ids = []
    for i in range(5):
        ok, r = await call(D, "task_create", room="pack-main", title=f"t{i}", body="", assignee_name="alice")
        ids.append(r["task_id"])
    ok, r = await call(D, "task_list")
    got = [t["id"] for t in r["tasks"]]
    check("default page returns all 5 ascending", got == ids, str(got))
    ok, r = await call(D, "task_list", offset=3)
    got2 = [t["id"] for t in r["tasks"]]
    check("offset=3 returns last 2", got2 == ids[3:], str(got2))
    ok, r = await call(D, "task_list", offset=99)
    check("offset beyond end returns empty", ok and r["tasks"] == [], str(r)[:60])
    ok, r = await call(D, "task_list", offset=-2)
    got3 = [t["id"] for t in r["tasks"]]
    check("negative offset clamped to 0", got3 == ids, str(got3))
    print(f"\n{PASS} passed, {FAIL} failed")
    return FAIL

sys.exit(1 if asyncio.run(main()) else 0)
