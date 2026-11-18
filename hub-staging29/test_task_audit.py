"""staging29 定向测试：task_audit（任务完整轨迹查询，定稿 §11.1 审计入口）。"""
import asyncio, sys
sys.path.insert(0, ".")
from harness import call, setup_members, TOKENS

PASS = FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  ok {name}")
    else:
        FAIL += 1; print(f"  FAIL {name} {extra}")

async def main():
    await setup_members()
    D = TOKENS["dispatcher"]; A = TOKENS["alice"]; B = TOKENS["bob"]

    # 造 12 张卡，让 task 1 与 task 12 同时存在（前缀碰撞检查）
    ids = []
    for i in range(12):
        ok, t = await call(D, "task_create", room="pack-main",
                           title=f"audit-drill-{i+1}", body="drill")
        assert ok, t
        ids.append(t["task_id"] if "task_id" in t else t["id"])
    t1, t12 = ids[0], ids[11]
    check("12 张卡建成", t1 == 1 and t12 == 12, f"{t1},{t12}")

    # task 12 上做一串动作，task 1 保持只有 create
    ok, cl = await call(A, "task_claim", task_id=t12); assert ok, cl
    tok12 = cl["claim_token"]
    ok, rs = await call(A, "run_start", task_id=t12, claim_token=tok12); assert ok, rs
    ok, tr = await call(A, "task_transfer", task_id=t12, to_member="bob",
                        claim_token=tok12, reason="audit drill"); assert ok, tr
    ok, ac = await call(B, "task_accept_transfer", task_id=t12); assert ok, ac
    tok12b = ac["claim_token"]
    ok, cp = await call(B, "task_complete", task_id=t12, claim_token=tok12b,
                        result="done", envelope={"verdict": "done", "summary": "drill ok"})
    assert ok, cp
    ok, rv = await call(D, "task_review", task_id=t12, approve=True, note="audit pass")
    assert ok, rv

    # --- task_audit(12)：全轨迹 ---
    ok, au = await call(D, "task_audit", task_id=t12)
    check("audit 调用成功", ok, str(au))
    check("task 基本信息", au["task"]["id"] == t12 and au["task"]["status"] == "done"
          and au["task"]["created_by"] == "dispatcher"
          and au["task"]["review_note"] == "audit pass", str(au["task"]))
    acts = [e["action"] for e in au["events"]]
    for want in ("task_create", "task_claim", "run_start", "task_transfer",
                 "task_accept_transfer", "task_complete", "task_review"):
        check(f"事件含 {want}", want in acts, str(acts))
    tss = [e["ts"] for e in au["events"]]
    check("事件按时间正序", tss == sorted(tss))
    check("runs 台账 1 条 fence=1", len(au["runs"]) == 1 and au["runs"][0]["fence"] == 1,
          str(au["runs"]))
    check("transfers 1 条已接受", len(au["transfers"]) == 1
          and au["transfers"][0]["status"] == "accepted", str(au["transfers"]))
    check("未截断", au["events_truncated"] is False)

    # --- task_audit(1)：不得串入 task 12 的事件 ---
    ok, au1 = await call(D, "task_audit", task_id=t1)
    acts1 = [e["action"] for e in au1["events"]]
    check("task1 只有自己的 create", acts1 == ["task_create"], str(acts1))
    check("task1 runs/transfers 为空", au1["runs"] == [] and au1["transfers"] == [])

    # --- 权限与错误 ---
    ok, _ = await call(A, "task_audit", task_id=t12)
    check("成员调用被拒（调度员专属）", not ok)
    ok, _ = await call("bogus-token", "task_audit", task_id=t12)
    check("伪造 token 被拒", not ok)
    ok, _ = await call(D, "task_audit", task_id=9999)
    check("不存在的任务报错", not ok)

    print(f"\n{PASS} passed, {FAIL} failed")
    return FAIL

if __name__ == "__main__":
    raise SystemExit(1 if asyncio.run(main()) else 0)
