"""staging27 定向测试：task_list 新增 created_by / review_note 两字段。

场景：
1) 调度员建卡指派 alice → task_list 里 created_by == "dispatcher"、review_note 为 None。
2) 对照卡 → created_by 同为成员名字符串（验 JOIN 取名而非原始 id）。
3) alice 认领并交活 → 调度员打回并给理由 → task_list 里该卡 status 回 open、
   review_note == 理由原文、assignee 被清空；alice 视角同样能读到两字段。
4) 再次交活 → 调度员通过并给备注 → status == done、review_note == 通过备注。
5) 字段不串卡：另一张卡的 review_note 不受影响。
"""
import asyncio, sys
sys.path.insert(0, "/home/hatch/workspace/pack/hub-staging27")
from harness import call, setup_members, TOKENS

FAILS = []

def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f" | {extra}" if extra and not cond else ""))
    if not cond:
        FAILS.append(name)

async def main():
    await setup_members()
    disp, alice, owner = TOKENS["dispatcher"], TOKENS["alice"], TOKENS["owner"]

    ok, r = await call(disp, "task_create", room="pack-main", title="字段测试卡", body="验 created_by 与 review_note", assignee_name="alice")
    check("建卡成功", ok and "task_id" in r, str(r))
    t1 = r["task_id"]

    ok, r = await call(disp, "task_create", room="pack-main", title="对照卡", body="review_note 不串卡对照")
    check("对照卡建卡成功", ok and "task_id" in r, str(r))
    t2 = r["task_id"]

    ok, r = await call(disp, "task_list")
    tasks = {t["id"]: t for t in r["tasks"]}
    check("created_by=dispatcher", tasks[t1].get("created_by") == "dispatcher", str(tasks[t1]))
    check("created_by 是成员名而非 id", tasks[t2].get("created_by") == "dispatcher", str(tasks[t2].get("created_by")))
    check("review_note 初始为空", tasks[t1].get("review_note") is None, str(tasks[t1].get("review_note")))
    check("旧字段仍在", all(k in tasks[t1] for k in ("assignee", "envelope", "backoff_remaining_sec", "fail_class")))

    ok, r = await call(alice, "task_claim", task_id=t1)
    check("alice 认领", ok and r.get("claim_token"), str(r))
    tok = r["claim_token"]
    ok, r = await call(alice, "task_complete", task_id=t1, claim_token=tok,
                       result="第一版成果", envelope={"verdict": "done", "summary": "做完了"})
    check("alice 交活", ok, str(r))

    ok, r = await call(disp, "task_review", task_id=t1, approve=False, note="理由：少了边界处理")
    check("调度员打回", ok, str(r))
    ok, r = await call(alice, "task_list")
    tasks = {t["id"]: t for t in r["tasks"]}
    check("打回后回 open", tasks[t1]["status"] == "open", tasks[t1]["status"])
    check("review_note=打回理由", tasks[t1].get("review_note") == "理由：少了边界处理", str(tasks[t1].get("review_note")))
    check("打回后指派清空", tasks[t1].get("assignee") is None, str(tasks[t1].get("assignee")))
    check("另一卡 review_note 不串", tasks[t2].get("review_note") is None)

    ok, r = await call(disp, "task_clear_backoff", task_id=t1)
    check("调度员清退避（打回后按规则有退避）", ok, str(r))
    ok, r = await call(alice, "task_claim", task_id=t1)
    check("清退避后二次认领", ok and bool(r.get("claim_token")), str(r))
    tok = r["claim_token"]
    ok, r = await call(alice, "task_complete", task_id=t1, claim_token=tok,
                       result="第二版成果", envelope={"verdict": "done", "summary": "补好边界"})
    check("二次交活", ok, str(r))
    ok, r = await call(disp, "task_review", task_id=t1, approve=True, note="通过备注：可以")
    check("调度员通过", ok, str(r))
    ok, r = await call(disp, "task_list")
    tasks = {t["id"]: t for t in r["tasks"]}
    check("通过后 done", tasks[t1]["status"] == "done", tasks[t1]["status"])
    check("review_note=通过备注", tasks[t1].get("review_note") == "通过备注：可以", str(tasks[t1].get("review_note")))

    print("RESULT:", "ALL PASS" if not FAILS else f"{len(FAILS)} FAIL: {FAILS}")
    sys.exit(1 if FAILS else 0)

asyncio.run(main())
