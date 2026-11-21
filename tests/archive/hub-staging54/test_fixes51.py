"""staging51 红队修复专项用例（2026-11-20）：对应 REVIEW51「红队修复」
一节的 6 处改动中可在 HTTP 层确定验证的 4 处——
① task_transfer 目标为 external 必须拒（成员自转 + 调度员强制双向），
   且任务/转交表零变化；另设内部成员正向对照（转交通道本身没坏）。
③ member_token_rotate("dispatcher") 必须拒且哈希不变。
⑤ pair_code_create(reclaim=True) 对不存在成员 / 内部成员 / 房间不
   一致三种情形发码即拒；合法 reclaim 仍可发码兑换、只换 token。
⑥ task_schedule 给 external 跨房 / 引擎标记（标题、正文各一）必须
   permanent 拒；合法指派可排班。
④（附件 >5MB 审计）在 test_direct.py 进程内直测补断言（HTTP 传输层
   4MiB 上限到不了 hub 检查）；②（task_claim TOCTOU）为结构修复，
   由主套件 + 并发套件既有 claim 用例回归覆盖。

本套件挂在主套件之后跑（run_tests.sh），共用同一隔离中枢与 DB，
夹具名一律 fx- 前缀避免与主套件相撞。
"""
import asyncio, json, os, sqlite3, sys, time
sys.path.insert(0, ".")
from harness import call, try_call, pair_post, DISP, DB

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


def es(err):
    return err[:100] if isinstance(err, str) else ""


async def make_external(name, provider="codex", room="pack-main", reclaim=False):
    r = await call(DISP, "pair_code_create", member_name=name, provider=provider,
                   room=room, reclaim=reclaim)
    st, resp = pair_post({"code": r["pair_code"]})
    assert st == 200, (name, st, resp)
    return r, resp


async def main():
    # ---------- 夹具 ----------
    reg = await call(DISP, "member_register", name="fx-carol")
    CAROL = reg["token"]
    reg = await call(DISP, "member_register", name="fx-dave")
    DAVE = reg["token"]
    await call(DISP, "room_create", name="fx-room2")
    _, resp = await make_external("fx-ext")
    EXT = resp["token"]
    EXT_ID = resp["member_id"]
    carol_id = dbq("SELECT id FROM members WHERE name='fx-carol'")[0]["id"]

    # ---------- ⑤ pair_code_create(reclaim=True) 目标校验 ----------
    ok, err = await try_call(DISP, "pair_code_create", member_name="fx-ghost",
                             provider="codex", room="pack-main", reclaim=True)
    note("fix5: reclaim for nonexistent member rejected at create",
         not ok and "reclaim target" in err, es(err))
    ok, err = await try_call(DISP, "pair_code_create", member_name="fx-carol",
                             provider="codex", room="pack-main", reclaim=True)
    note("fix5: reclaim for internal member rejected at create",
         not ok and "internal member" in err, es(err))
    ok, err = await try_call(DISP, "pair_code_create", member_name="fx-ext",
                             provider="codex", room="fx-room2", reclaim=True)
    note("fix5: reclaim with mismatched room rejected at create",
         not ok and "reclaim room mismatch" in err, es(err))
    ghost_codes = dbq("SELECT COUNT(*) AS n FROM pair_codes WHERE member_name='fx-ghost'")
    note("fix5: rejected reclaim creates no pair code row", ghost_codes[0]["n"] == 0)
    # 合法 reclaim：同房发码 → 兑换成功、member_id 不变、只换 token
    rc = await call(DISP, "pair_code_create", member_name="fx-ext", provider="codex",
                    room="pack-main", reclaim=True)
    st, resp2 = pair_post({"code": rc["pair_code"]})
    note("fix5: legal reclaim redeems, same member",
         st == 200 and resp2["member_id"] == EXT_ID, f"st={st}")
    ok, _ = await try_call(EXT, "heartbeat")
    note("fix5: reclaim kills old token", not ok)
    EXT = resp2["token"]
    ok, hb = await try_call(EXT, "heartbeat")
    note("fix5: reclaimed token works", ok and hb.get("member") == "fx-ext")

    # ---------- ① task_transfer 目标为 external 必须拒 ----------
    t = await call(DISP, "task_create", room="pack-main", title="fx 转交测试卡",
                   body="红队修复①夹具", assignee_name="fx-carol")
    tid = t["task_id"]
    cl = await call(CAROL, "task_claim", task_id=tid)
    ctok = cl["claim_token"]
    before = dbq("SELECT assignee, status, claim_token FROM tasks WHERE id=?", (tid,))[0]
    ok, err = await try_call(CAROL, "task_transfer", task_id=tid, claim_token=ctok,
                             to_member="fx-ext", reason="试着转给外部成员")
    note("fix1: member transfer to external rejected",
         not ok and "external member" in err, es(err))
    ok, err = await try_call(DISP, "task_transfer", task_id=tid, claim_token="",
                             to_member="fx-ext", reason="调度员强制转交")
    note("fix1: dispatcher forced transfer to external rejected",
         not ok and "external member" in err, es(err))
    after = dbq("SELECT assignee, status, claim_token FROM tasks WHERE id=?", (tid,))[0]
    n_tr = dbq("SELECT COUNT(*) AS n FROM task_transfers WHERE task_id=?", (tid,))[0]["n"]
    note("fix1: rejected transfers left task + transfer table untouched",
         after["assignee"] == before["assignee"] == carol_id
         and after["status"] == "claimed"
         and after["claim_token"] == before["claim_token"] and n_tr == 0,
         f"n_tr={n_tr}")
    # 正向对照：转给内部成员仍可走通（新检查只拦 external）
    tr = await call(CAROL, "task_transfer", task_id=tid, claim_token=ctok,
                    to_member="fx-dave", reason="正向对照")
    note("fix1: transfer to internal member still works",
         tr["transfer_status"] == "pending" and tr["to_member"] == "fx-dave")

    # ---------- ③ member_token_rotate 不许碰 dispatcher ----------
    h0 = dbq("SELECT token_hash FROM members WHERE name='dispatcher'")[0]["token_hash"]
    ok, err = await try_call(DISP, "member_token_rotate", name="dispatcher")
    note("fix3: rotating dispatcher token rejected",
         not ok and "dispatcher" in err, es(err))
    h1 = dbq("SELECT token_hash FROM members WHERE name='dispatcher'")[0]["token_hash"]
    note("fix3: dispatcher token hash unchanged", h0 == h1)

    # ---------- ⑥ task_schedule 对 external 的拦截 ----------
    future = time.time() + 3600
    ok, err = await try_call(DISP, "task_schedule", room="fx-room2",
                             title="fx 跨房排班", body="b", member_name="fx-ext",
                             at_epoch=future)
    note("fix6: schedule cross-room for external -> permanent reject",
         not ok and "permanent:" in err, es(err))
    ok, err = await try_call(DISP, "task_schedule", room="pack-main",
                             title="[M4A] fx 引擎排班", body="b", member_name="fx-ext",
                             at_epoch=future)
    note("fix6: schedule pinned(title) for external -> permanent reject",
         not ok and "permanent:" in err, es(err))
    ok, err = await try_call(DISP, "task_schedule", room="pack-main",
                             title="fx 普通标题", body="正文含 [ENG] 标记",
                             member_name="fx-ext", at_epoch=future)
    note("fix6: schedule pinned(body) for external -> permanent reject",
         not ok and "permanent:" in err, es(err))
    sc = await call(DISP, "task_schedule", room="pack-main", title="fx 合法排班",
                    body="b", member_name="fx-ext", at_epoch=future)
    srow = dbq("SELECT assignee, status FROM tasks WHERE id=?", (sc["task_id"],))[0]
    note("fix6: legal schedule for external created",
         srow["assignee"] == EXT_ID and srow["status"] == "open")
    n_dead = dbq("SELECT COUNT(*) AS n FROM tasks WHERE title IN"
                 " ('fx 跨房排班','[M4A] fx 引擎排班','fx 普通标题')")[0]["n"]
    note("fix6: rejected schedules created no tasks", n_dead == 0)

    print(f"== {sum(res)}/{len(res)} PASS", flush=True)
    if not all(res):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
