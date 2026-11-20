import asyncio, sqlite3, sys
from concurrent.futures import ThreadPoolExecutor
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


async def main():
    # 1. 同一配对码 8 路并发兑换：只许一个成功（_lock 串行 + 一码一成员）
    for run in (1, 2):
        pc = await call(DISP, "pair_code_create", member_name=f"ext-conc{run}",
                        provider="codex", room="pack-main")
        code = pc["pair_code"]
        with ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(lambda _: pair_post({"code": code}), range(8)))
        oks = [r for r in results if r[0] == 200]
        n = dbq("SELECT COUNT(*) AS n FROM members WHERE name=?",
                (f"ext-conc{run}",))[0]["n"]
        note(f"conc run{run}: 8 parallel redeems of one code -> exactly 1 success",
             len(oks) == 1 and n == 1, f"oks={len(oks)} members={n}")
        crow = dbq("SELECT fail_count FROM pair_codes WHERE id=?", (pc["code_id"],))[0]
        note(f"conc run{run}: losing redeems counted as failures",
             crow["fail_count"] == 7, f"fail_count={crow['fail_count']}")

    # 2. 两个码抢同一个新名字（均非 reclaim）：只许一个建号成功
    pc1 = await call(DISP, "pair_code_create", member_name="ext-race",
                     provider="codex", room="pack-main")
    pc2 = await call(DISP, "pair_code_create", member_name="ext-race",
                     provider="gemini", room="pack-main")
    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(pair_post, [{"code": pc1["pair_code"]},
                                          {"code": pc2["pair_code"]}]))
    oks = [r for r in results if r[0] == 200]
    n = dbq("SELECT COUNT(*) AS n FROM members WHERE name='ext-race'")[0]["n"]
    note("conc: two codes racing for one name -> exactly 1 success",
         len(oks) == 1 and n == 1, f"oks={len(oks)} members={n}")

    # 3. 两名内部成员并发认领同一张未指派卡：只许一个赢
    ra = await call(DISP, "member_register", name="c-alice")
    rb = await call(DISP, "member_register", name="c-bob")
    tid = (await call(DISP, "task_create", room="pack-main", title="race claim",
                      body="b", assignee_name=""))["task_id"]
    r1, r2 = await asyncio.gather(
        try_call(ra["token"], "task_claim", task_id=tid),
        try_call(rb["token"], "task_claim", task_id=tid))
    note("conc: parallel claim -> exactly one winner",
         (r1[0] and not r2[0]) or (r2[0] and not r1[0]),
         f"r1={r1[0]} r2={r2[0]}")

    # 4. 同一条消息 4 路并发删除：只许一个成功（既有 C3 探针回归）
    mp = await call(ra["token"], "room_post", room="pack-main", kind="chat", body="del me")
    results = await asyncio.gather(*[
        try_call(ra["token"], "message_delete", message_id=mp["message_id"])
        for _ in range(4)])
    succ = sum(1 for ok, _ in results if ok)
    note("conc: parallel delete same message -> exactly 1 success", succ == 1,
         f"succ={succ}")

    # 5. external 10 路并发发帖：分钟窗口 5 条硬顶不越窗（_lock 串行计数）
    pc = await call(DISP, "pair_code_create", member_name="ext-burst",
                    provider="codex", room="pack-main")
    st, resp = pair_post({"code": pc["pair_code"]})
    assert st == 200, resp
    btok = resp["token"]
    results = await asyncio.gather(*[
        try_call(btok, "room_post", room="pack-main", kind="chat", body=f"burst{i}")
        for i in range(10)])
    succ = sum(1 for ok, _ in results if ok)
    limited = sum(1 for ok, e in results if not ok and "rate_limited" in e)
    note("conc: 10 parallel external posts -> exactly 5 pass, 5 rate-limited",
         succ == 5 and limited == 5, f"succ={succ} limited={limited}")

    print(f"== {sum(res)}/{len(res)} PASS", flush=True)
    if not all(res):
        sys.exit(1)


asyncio.run(main())
