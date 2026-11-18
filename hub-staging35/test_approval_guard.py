#!/usr/bin/env python3
"""staging35：approval_request 门口守卫回归（第 1 轮深查 F1 + 第 7 轮 S2）。
跑法：先起 staging35 hub（端口 8835），再
  ../hub/.venv/bin/python test_approval_guard.py
"""
import asyncio, sqlite3, sys
sys.path.insert(0, ".")
from harness import call, setup_members, TOKENS

DB = "/home/hatch/workspace/pack/hub-staging35/pack.db"
PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS {name}")
    else:
        FAIL += 1
        print(f"FAIL {name} {extra}")


def approval_msgs(task_ref=None):
    c = sqlite3.connect(DB)
    rows = c.execute(
        "SELECT id, author_id, task_id, body FROM messages WHERE kind='approval_request'"
    ).fetchall()
    c.close()
    if task_ref is not None:
        rows = [r for r in rows if r[2] == task_ref or f"#{task_ref}" in (r[3] or "")]
    return rows


async def main():
    await setup_members()
    disp, alice, bob = TOKENS["dispatcher"], TOKENS["alice"], TOKENS["bob"]

    ok, t1 = await call(disp, "task_create", room="pack-main",
                        title="【需批准】staging35 gate test", body="b", assignee_name="alice")
    assert ok, t1
    t1 = t1["task_id"]
    ok, cl = await call(alice, "task_claim", task_id=t1)
    check("alice claims T1", ok, str(cl))

    body1 = f"\u4efb\u52a1#{t1}\u300cstaging35\u300d\u9700\u8981\u7fa4\u4e3b\u6279\u51c6\u624d\u80fd\u6267\u884c\uff0c\u8bf7\u8c03\u5ea6\u5458\u786e\u8ba4\u3002"

    ok, r = await call(alice, "room_post", room="pack-main", body=body1, kind="approval_request")
    check("holder approval_request ok", ok, str(r))
    rows = approval_msgs(t1)
    check("holder request persisted with task_id", len(rows) == 1 and rows[0][2] == t1, str(rows))

    ok, r = await call(bob, "room_post", room="pack-main", body=body1, kind="approval_request")
    check("non-holder forged rejected", not ok, str(r))
    check("forged request not persisted", len(approval_msgs(t1)) == 1, str(approval_msgs(t1)))

    ok, r = await call(alice, "room_post", room="pack-main",
                       body="\u4efb\u52a1#99999\u300cx\u300d\u9700\u8981\u6279\u51c6", kind="approval_request")
    check("unknown task (member) rejected", not ok, str(r))
    ok, r = await call(disp, "room_post", room="pack-main",
                       body="\u4efb\u52a1#99999\u300cx\u300d\u9700\u8981\u6279\u51c6", kind="approval_request")
    check("unknown task (dispatcher) rejected", not ok, str(r))

    ok, r = await call(alice, "room_post", room="pack-main",
                       body="needs approval", kind="approval_request")
    check("no task ref rejected", not ok, str(r))

    ok, t2 = await call(disp, "task_create", room="pack-main",
                        title="staging35 unclaimed", body="b", assignee_name="alice")
    t2 = t2["task_id"]
    ok, r = await call(alice, "room_post", room="pack-main",
                       body=f"\u4efb\u52a1#{t2}\u300cx\u300d\u9700\u8981\u6279\u51c6", kind="approval_request")
    check("own but unclaimed rejected", not ok, str(r))

    ok, r = await call(disp, "room_post", room="pack-main", body=body1, kind="approval_request")
    check("dispatcher on-behalf ok", ok, str(r))

    before = len(approval_msgs())
    results = await asyncio.gather(*[
        call(bob, "room_post", room="pack-main", body=body1, kind="approval_request")
        for _ in range(6)])
    check("parallel forgeries all rejected", all(not ok for ok, _ in results), str(results))
    check("parallel forgeries zero persisted", len(approval_msgs()) == before,
          f"{before} -> {len(approval_msgs())}")

    ok, r = await call(bob, "room_post", room="pack-main", body="hello", kind="chat")
    check("plain chat unaffected", ok, str(r))
    ok, r = await call(bob, "room_post", room="pack-main", body="\u6279\u51c6 \u4efb\u52a1#1", kind="decision")
    check("decision guard intact", not ok, str(r))
    ok, r = await call(bob, "room_post", room="pack-main", body="x", kind="task_card")
    check("task_card guard intact", not ok, str(r))
    ok, r = await call(alice, "room_post", room="pack-main", body="started", kind="report")
    check("report unaffected", ok, str(r))

    print(f"RESULT pass={PASS} fail={FAIL}")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
