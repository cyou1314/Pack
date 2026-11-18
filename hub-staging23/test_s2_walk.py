"""staging23：S2 加载契约全程走一遍（成员视角）。
发布真实三技能 → 指派 → 成员拉目录（测字节数 ≤4096）→ 按需读正文 →
认领任务 → 信封 self_check 留痕交活 → 验收存储。"""
import asyncio, json, sys
sys.path.insert(0, "/home/hatch/workspace/pack/hub-staging23")
import harness
from harness import call

PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" | {extra}" if extra else ""))

SKILLS = {
    "delivery-envelope": "交活信封规范：认领/开工/交活的字段与纪律",
    "egress-discipline": "数据出口纪律：外发前先 egress_check 的判定与留痕",
    "memory-protocol": "记忆协议：分层读写、硬顶与防毒化口径",
}

async def main():
    D = harness.DISP
    ok, reg = await call(D, "member_register", name="absent")
    M = reg["token"]
    # 发布真实技能正文（仓库 skills/ 正本）并指派
    for name, desc in SKILLS.items():
        body = open(f"/home/hatch/workspace/pack/repo-mirror/skills/{name}.md").read()
        ok, out = await call(D, "skill_publish", name=name, body=body, description=desc)
        check(f"publish {name}", ok, str(out)[:60])
        ok, out = await call(D, "skill_assign", name=name, member_name="absent")
        check(f"assign {name}", ok)
    # 再发布一个不指派的，验证目录隔离
    ok, _ = await call(D, "skill_publish", name="unassigned-x", body="不应出现", description="未指派")

    # 成员拉目录：只见指派给自己的三个；目录序列化 ≤4096 字节（S2 契约）
    ok, out = await call(M, "skill_list")
    names = [s["name"] for s in out["skills"]]
    check("directory only assigned", names == sorted(SKILLS), str(names))
    blob = json.dumps(out["skills"], ensure_ascii=False).encode()
    check("directory ≤4096 bytes", len(blob) <= 4096, f"{len(blob)} bytes")
    check("directory has no bodies", all("body" not in s for s in out["skills"]))

    # 按需读正文
    for name in SKILLS:
        ok, out = await call(M, "skill_read", name=name)
        check(f"read {name}", ok and len(out["body"]) > 100, f"len={len(out.get('body','')) if ok else out}")

    # 任务全程：派活 → 认领 → run_start → 带 self_check 的信封交活
    ok, t = await call(D, "task_create", room="pack-main", title="S2 契约演练",
                       body="照 delivery-envelope 规程交活", assignee_name="absent")
    tid = t["task_id"]
    ok, cl = await call(M, "task_claim", task_id=tid)
    tok = cl["claim_token"]
    ok, rs = await call(M, "run_start", task_id=tid, claim_token=tok)
    check("run_start fence", ok and rs.get("fence", 0) >= 1, str(rs)[:60])
    env = {"verdict": "done", "summary": "S2 契约演练完成",
           "artifacts": [], "self_check": "已按 delivery-envelope 规程核对：run_start 留痕、信封字段齐全",
           "known_issues": [], "followups": []}
    ok, out = await call(M, "task_complete", task_id=tid, claim_token=tok,
                         result="done", envelope=env)
    check("complete with envelope", ok, str(out)[:80])
    ok, out = await call(D, "task_list")
    task = [x for x in out["tasks"] if x["id"] == tid][0]
    stored = task.get("envelope")
    if isinstance(stored, str):
        stored = json.loads(stored)
    check("envelope stored with self_check",
          bool(stored) and "delivery-envelope" in (stored.get("self_check") or ""),
          str(stored)[:80])
    check("task in review", task["status"] == "review")

    print(f"RESULT pass={len(PASS)} fail={len(FAIL)}")
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
