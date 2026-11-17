#!/usr/bin/env python3
"""staging38: member_list week_done (D12 weekly completions aggregate).
Run: start staging38 hub (port 8838), then ../hub/.venv/bin/python test_week_done.py
"""
import asyncio, sqlite3, sys, time, datetime
sys.path.insert(0, ".")
from harness import call, setup_members, TOKENS

DB = "/home/hatch/workspace/pack/hub-staging38/pack.db"
PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS {name}")
    else:
        FAIL += 1
        print(f"FAIL {name} {extra}")


def week_done(lst, name):
    for m in lst["members"]:
        if m["name"] == name:
            return m.get("week_done")
    return None


async def finish(disp, alice, title):
    ok, t = await call(disp, "task_create", room="pack-main", title=title, body="b", assignee_name="alice")
    tid = t["task_id"]
    ok, cl = await call(alice, "task_claim", task_id=tid)
    await call(alice, "task_complete", task_id=tid, claim_token=cl["claim_token"], result="r")
    await call(disp, "task_review", task_id=tid, approve=True, note="")
    return tid


async def main():
    await setup_members()
    disp, alice, bob = TOKENS["dispatcher"], TOKENS["alice"], TOKENS["bob"]

    ok, lst = await call(alice, "member_list")
    check("week_done present and 0 for all at start",
          all(m.get("week_done") == 0 for m in lst["members"]), str(lst)[:200])

    t1 = await finish(disp, alice, "wd one")
    t2 = await finish(disp, alice, "wd two")
    ok, lst = await call(disp, "member_list")
    check("alice week_done=2 after two completions", week_done(lst, "alice") == 2, str(week_done(lst, "alice")))
    check("bob week_done=0", week_done(lst, "bob") == 0)
    check("dispatcher week_done=0", week_done(lst, "dispatcher") == 0)

    # a task still in review does not count
    ok, t = await call(disp, "task_create", room="pack-main", title="wd pending", body="b", assignee_name="alice")
    tid = t["task_id"]
    ok, cl = await call(alice, "task_claim", task_id=tid)
    await call(alice, "task_complete", task_id=tid, claim_token=cl["claim_token"], result="r")
    ok, lst = await call(disp, "member_list")
    check("review-state task not counted", week_done(lst, "alice") == 2, str(week_done(lst, "alice")))
    await call(disp, "task_cancel", task_id=tid)

    # backdate one done task to last week -> count drops
    tz = datetime.timezone(datetime.timedelta(hours=8))
    now = datetime.datetime.fromtimestamp(time.time(), tz)
    monday = (now - datetime.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    last_week = monday.timestamp() - 3600
    c = sqlite3.connect(DB)
    c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (last_week, t1))
    c.commit(); c.close()
    ok, lst = await call(disp, "member_list")
    check("last-week completion excluded", week_done(lst, "alice") == 1, str(week_done(lst, "alice")))

    # boundary: exactly at Monday 00:00 CST counts
    c = sqlite3.connect(DB)
    c.execute("UPDATE tasks SET updated_at=? WHERE id=?", (monday.timestamp(), t1))
    c.commit(); c.close()
    ok, lst = await call(disp, "member_list")
    check("monday 00:00 boundary inclusive", week_done(lst, "alice") == 2, str(week_done(lst, "alice")))

    print(f"RESULT pass={PASS} fail={FAIL}")

asyncio.run(main())
