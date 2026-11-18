#!/usr/bin/env python3
"""staging36：加急（D15 / 定稿 §15.3）回归。
跑法：先起 staging36 hub（端口 8836），再 ../hub/.venv/bin/python test_urgent.py
"""
import asyncio, sqlite3, sys
sys.path.insert(0, ".")
from harness import call, setup_members, TOKENS

DB = "/home/hatch/workspace/pack/hub-staging36/pack.db"
PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS {name}")
    else:
        FAIL += 1
        print(f"FAIL {name} {extra}")


async def main():
    await setup_members()
    disp, alice, bob = TOKENS["dispatcher"], TOKENS["alice"], TOKENS["bob"]

    cols = [r[1] for r in sqlite3.connect(DB).execute("PRAGMA table_info(tasks)")]
    check("migration: urgent column exists", "urgent" in cols, str(cols))

    ok, a = await call(disp, "task_create", room="pack-main", title="A normal first", body="b", assignee_name="alice")
    ok, b = await call(disp, "task_create", room="pack-main", title="B normal second", body="b", assignee_name="alice")
    ok, c = await call(disp, "task_create", room="pack-main", title="C urgent third", body="b", assignee_name="alice", urgent=True)
    check("task_create urgent=True echoes urgent", ok and c.get("urgent") is True, str(c))
    check("task_create default echoes urgent False", a.get("urgent") is False, str(a))
    A, B, C = a["task_id"], b["task_id"], c["task_id"]

    ok, lst = await call(alice, "task_list", status="open", assignee_name="alice")
    ids = [t["id"] for t in lst["tasks"]]
    check("open list: urgent task first", ids[0] == C, str(ids))
    urg = {t["id"]: t["urgent"] for t in lst["tasks"]}
    check("list carries urgent flags", urg.get(C) == 1 and urg.get(A) == 0, str(urg))
    check("non-urgent keep id order", ids[1:] == [A, B], str(ids))

    ok, r = await call(disp, "task_set_urgent", task_id=A, urgent=True)
    check("set_urgent on open ok", ok and r.get("urgent") is True, str(r))
    ok, lst = await call(alice, "task_list", status="open", assignee_name="alice")
    ids = [t["id"] for t in lst["tasks"]]
    check("two urgents: id order within urgent group", ids[:2] == [A, C], str(ids))

    ok, r = await call(disp, "task_set_urgent", task_id=A, urgent=True)
    check("set_urgent idempotent", ok, str(r))
    ok, r = await call(disp, "task_set_urgent", task_id=A, urgent=False)
    check("unset urgent ok", ok and r.get("urgent") is False, str(r))

    ok, r = await call(bob, "task_set_urgent", task_id=B, urgent=True)
    check("member set_urgent refused", not ok, str(r))
    ok, r = await call(disp, "task_set_urgent", task_id=99999, urgent=True)
    check("unknown task refused", not ok, str(r))

    ok, r = await call(disp, "task_cancel", task_id=B)
    check("cancel B", ok, str(r))
    ok, r = await call(disp, "task_set_urgent", task_id=B, urgent=True)
    check("cancelled task set_urgent refused", not ok, str(r))

    ok, cl = await call(alice, "task_claim", task_id=C)
    check("urgent task claimable", ok, str(cl))
    ok, r = await call(disp, "task_set_urgent", task_id=C, urgent=False)
    check("claimed task can unset urgent", ok, str(r))

    rows = sqlite3.connect(DB).execute(
        "SELECT detail FROM audit WHERE action='task_set_urgent'").fetchall()
    check("audit rows written", len(rows) >= 4, str(rows))

    print(f"RESULT pass={PASS} fail={FAIL}")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
