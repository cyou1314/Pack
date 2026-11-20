"""staging54（搬运二·队列显性化）：task_list 新增 queue_ahead 字段。

口径（PORT-QUEUE-BRIEF）：按 assignee 分组（NULL = 未指派池），组内只计
open/claimed/working、排序 urgent DESC, id ASC，queue_ahead = 排在前面
的同组任务数（队首 0）；review 与终结状态为 null；位次按全队列算，
过滤/分页不改变口径；旧字段、旧排序、工具数（74）一律回归不破。
在 run_tests.sh 已起的隔离中枢（18799、全新 DB）上跑。
"""
import asyncio, sqlite3, sys
sys.path.insert(0, ".")
from harness import call, client, DISP, DB

res = []


def note(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  {extra}" if extra else ""), flush=True)


def dbq(sql, args=()):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return rows


async def main():
    # ---------- 0. 工具数仍 74 ----------
    async with client(DISP) as c:
        tools = await c.list_tools()
    note("tools == 75", len(tools) == 75, f"got {len(tools)}")

    ALICE = (await call(DISP, "member_register", name="alice"))["token"]
    BOB = (await call(DISP, "member_register", name="bob"))["token"]
    CAROL = (await call(DISP, "member_register", name="carol"))["token"]

    async def make(assignee, title, urgent=False, room="pack-main"):
        r = await call(DISP, "task_create", room=room, title=title, body="b",
                       assignee_name=assignee, urgent=urgent)
        return r["task_id"]

    async def ranks(**kw):
        r = await call(DISP, "task_list", **kw)
        return {t["id"]: t for t in r["tasks"]}, r["tasks"]

    # ---------- A. 单组位次 + urgent 插队 + claimed/working 仍在队列 ----------
    A1 = await make("alice", "a1")
    A2 = await make("alice", "a2")
    A3 = await make("alice", "a3")
    m, _ = await ranks(assignee_name="alice")
    note("single group: open ranks 0,1,2",
         [m[A1]["queue_ahead"], m[A2]["queue_ahead"], m[A3]["queue_ahead"]] == [0, 1, 2],
         f"{[m[t]['queue_ahead'] for t in (A1, A2, A3)]}")
    A0 = await make("alice", "a0-urgent", urgent=True)
    m, _ = await ranks(assignee_name="alice")
    note("urgent jumps queue: ranks shift",
         [m[A0]["queue_ahead"], m[A1]["queue_ahead"], m[A2]["queue_ahead"],
          m[A3]["queue_ahead"]] == [0, 1, 2, 3],
         f"{[m[t]['queue_ahead'] for t in (A0, A1, A2, A3)]}")
    ct = (await call(ALICE, "task_claim", task_id=A1))["claim_token"]
    await call(ALICE, "task_update", task_id=A1, claim_token=ct, note="开工")
    m, _ = await ranks(assignee_name="alice")
    note("working stays in queue with same rank",
         m[A1]["status"] == "working" and m[A1]["queue_ahead"] == 1,
         f"status={m[A1]['status']} ahead={m[A1]['queue_ahead']}")

    # ---------- B. 多组互不干扰 ----------
    B1 = await make("bob", "b1")
    B2 = await make("bob", "b2")
    m, _ = await ranks(assignee_name="bob")
    note("second group ranks start at 0 despite alice's 4 active",
         [m[B1]["queue_ahead"], m[B2]["queue_ahead"]] == [0, 1],
         f"{[m[t]['queue_ahead'] for t in (B1, B2)]}")
    m, _ = await ranks(assignee_name="alice")
    note("alice ranks untouched by bob's tasks",
         [m[t]["queue_ahead"] for t in (A0, A1, A2, A3)] == [0, 1, 2, 3])

    # ---------- C. 未指派池单独成组 ----------
    U1 = await make("", "u1")
    U2 = await make("", "u2")
    m, rows = await ranks()
    note("unassigned pool ranks 0,1 in full list",
         [m[U1]["queue_ahead"], m[U2]["queue_ahead"]] == [0, 1],
         f"{[m[t]['queue_ahead'] for t in (U1, U2)]}")
    U3 = await make("", "u3-urgent", urgent=True)
    m, rows = await ranks()
    note("urgent in pool jumps pool queue only",
         [m[U3]["queue_ahead"], m[U1]["queue_ahead"], m[U2]["queue_ahead"]] == [0, 1, 2]
         and m[A0]["queue_ahead"] == 0 and m[B1]["queue_ahead"] == 0,
         f"pool={[m[t]['queue_ahead'] for t in (U3, U1, U2)]}")

    # ---------- D. review/done/blocked/cancelled 为 null 且不占位次 ----------
    ct2 = (await call(ALICE, "task_claim", task_id=A2))["claim_token"]
    await call(ALICE, "task_complete", task_id=A2, claim_token=ct2, result="done-a2")
    m, _ = await ranks(assignee_name="alice")
    note("review -> queue_ahead null, queue closes up",
         m[A2]["queue_ahead"] is None and m[A3]["queue_ahead"] == 2,
         f"A2={m[A2]['queue_ahead']} A3={m[A3]['queue_ahead']}")
    await call(DISP, "task_review", task_id=A2, approve=True)
    m, _ = await ranks(status="done")
    note("done -> queue_ahead null (field present)",
         A2 in m and "queue_ahead" in m[A2] and m[A2]["queue_ahead"] is None)
    ctb = (await call(BOB, "task_claim", task_id=B1))["claim_token"]
    await call(BOB, "task_block", task_id=B1, claim_token=ctb, reason="卡住")
    m, _ = await ranks(assignee_name="bob")
    note("blocked -> null and frees rank",
         m[B1]["queue_ahead"] is None and m[B2]["queue_ahead"] == 0,
         f"B1={m[B1]['queue_ahead']} B2={m[B2]['queue_ahead']}")
    await call(DISP, "task_cancel", task_id=B2)
    m, _ = await ranks(status="cancelled")
    note("cancelled -> queue_ahead null",
         B2 in m and m[B2]["queue_ahead"] is None)

    # ---------- F. 旧字段/排序/过滤回归 ----------
    OLD_KEYS = {"id", "title", "body", "status", "assignee", "attempts",
                "result", "envelope", "lease_expires", "updated_at",
                "fail_class", "next_claim_at", "sched_at", "urgent",
                "created_by", "review_note", "room", "backoff_remaining_sec"}
    m, rows = await ranks()
    note("every row carries old keys + queue_ahead",
         all(OLD_KEYS <= set(t.keys()) and "queue_ahead" in t for t in rows),
         f"rows={len(rows)}")
    keys = [(-(t["urgent"] or 0), t["id"]) for t in rows]
    note("list order unchanged (urgent DESC, id ASC)", keys == sorted(keys))
    m2, _ = await ranks(status="open,claimed")
    note("multi-status filter still works",
         all(t["status"] in ("open", "claimed") for t in m2.values())
         and len(m2) > 0, f"n={len(m2)}")
    await call(DISP, "room_create", name="room-b")
    RB = await make("alice", "room-b-task", room="room-b")
    m3, rows3 = await ranks(room="room-b")
    note("room filter returns only that room; rank is global per-assignee",
         [t["id"] for t in rows3] == [RB] and m3[RB]["queue_ahead"] == 3,
         f"ahead={m3[RB]['queue_ahead']} (alice actives ahead: A0,A1,A3)")

    # ---------- G. 与 DB 独立计算全量交叉核对 ----------
    act = dbq("SELECT id, assignee FROM tasks"
              " WHERE status IN ('open','claimed','working')"
              " ORDER BY urgent DESC, id ASC")
    expect, seen = {}, {}
    for r in act:
        g = r["assignee"]
        expect[r["id"]] = seen.get(g, 0)
        seen[g] = seen.get(g, 0) + 1
    m, rows = await ranks()
    bad = [(t["id"], t["queue_ahead"], expect.get(t["id"]))
           for t in rows
           if t["status"] in ("open", "claimed", "working")
           and t["queue_ahead"] != expect.get(t["id"])]
    nulls_ok = all(t["queue_ahead"] is None for t in rows
                   if t["status"] not in ("open", "claimed", "working"))
    note("full cross-check vs independent SQL computation",
         not bad and nulls_ok, f"bad={bad[:3]} rows={len(rows)}")

    # ---------- E. 分页：第二页位次按全队列算 ----------
    carol_ids = []
    for i in range(105):
        carol_ids.append(await make("carol", f"c{i}"))
    m1, page1 = await ranks(assignee_name="carol", offset=0)
    note("page1 has 100 rows, ranks 0..99",
         len(page1) == 100
         and [t["queue_ahead"] for t in page1] == list(range(100)),
         f"n={len(page1)}")
    m2, page2 = await ranks(assignee_name="carol", offset=100)
    note("page2 ranks continue 100..104 (not page-local 0..4)",
         [t["id"] for t in page2] == carol_ids[100:]
         and [t["queue_ahead"] for t in page2] == [100, 101, 102, 103, 104],
         f"{[t['queue_ahead'] for t in page2]}")
    m3, allrows = await ranks(offset=100)
    carol_p2 = [t for t in allrows if t["assignee"] == "carol"]
    note("unfiltered page2 keeps per-group full-queue ranks",
         len(carol_p2) > 0
         and all(t["queue_ahead"] == carol_ids.index(t["id"]) for t in carol_p2),
         f"carol rows on page2={len(carol_p2)}")

    print(f"== {sum(res)}/{len(res)} PASS")
    return 0 if all(res) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
