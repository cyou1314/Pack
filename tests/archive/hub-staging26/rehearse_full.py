"""staging26 全流程总演练（一条连续剧本走完整个产品）：
注册/头像 → 技能发布指派+额度种子 → watcher @转卡 → 成员认领/run_start →
GUI 任务拿桌面锁 ∥ 无头任务并行交活 → 信封交活 → 调度员验收 →
模板直发（第二房间）→ 排班真实钟 → 额度接力（absent→scout）→
摘要卡更新 → 终局核账 + 审计抽查 + DB 完整性。
阶段：seed（watcher 启动前）→ act1（@与并行）→ act2（模板/排班/接力/核账）。"""
import asyncio, json, sqlite3, sys, time
sys.path.insert(0, "/home/hatch/workspace/pack/hub-staging26")
import harness
from harness import call

DB = "/home/hatch/workspace/pack/hub-staging26/pack.db"
D = harness.DISP
TK = "/home/hatch/workspace/pack/hub-staging26/tokens.json"
PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" | {extra}" if extra else ""))

def tokens():
    return json.load(open(TK))

def insert_owner_msg(room, body):
    conn = sqlite3.connect(DB)
    oid = conn.execute("SELECT id FROM members WHERE name='owner'").fetchone()[0]
    rid = conn.execute("SELECT id FROM rooms WHERE name=?", (room,)).fetchone()[0]
    conn.execute("INSERT INTO messages(room_id, author_id, kind, body, created_at) VALUES (?,?, 'chat', ?, ?)",
                 (rid, oid, body, time.time()))
    conn.commit(); conn.close()

async def wait_task(title_part, timeout=40):
    for _ in range(timeout // 2):
        ok, out = await call(D, "task_list")
        hits = [t for t in out["tasks"] if title_part in t["title"]]
        if hits:
            return hits[0]
        await asyncio.sleep(2)
    return None

async def seed():
    T = {}
    for name in ("absent", "scout", "owner"):
        ok, out = await call(D, "member_register", name=name)
        T[name] = out["token"]
    json.dump(T, open(TK, "w"))
    ok, out = await call(D, "member_list")
    combos = {m["name"]: (m.get("avatar") or {}).get("combo") for m in out["members"]}
    check("注册+头像组合不撞", len(set(combos.values())) == 4, str(combos))
    descs = {"delivery-envelope": "交活信封规范：认领/开工/交活的字段与纪律",
             "egress-discipline": "数据出口纪律：外发前先 egress_check 的判定与留痕",
             "memory-protocol": "记忆协议：分层读写、硬顶与防毒化口径"}
    for name, desc in descs.items():
        body = open(f"/home/hatch/workspace/pack/repo-mirror/skills/{name}.md").read()
        await call(D, "skill_publish", name=name, body=body, description=desc)
        for m in ("absent", "scout"):
            await call(D, "skill_assign", name=name, member_name=m)
    ok, out = await call(T["absent"], "skill_list")
    blob = json.dumps(out["skills"], ensure_ascii=False).encode()
    check("技能目录 ≤4KB", len(blob) <= 4096, f"{len(blob)}B")
    ok, _ = await call(D, "room_create", name="room-x")
    ok2, _ = await call(D, "template_upsert", name="出图",
                        title_tpl="[ENG-image] 出图：{arg}",
                        body_tpl="按描述出图：{arg}", assignee="absent")
    check("建房+模板", ok and ok2)
    await call(D, "member_quota_set", member_name="absent", week_used_pct=62, extra_left=436000000)
    await call(D, "member_quota_set", member_name="scout", week_used_pct=10, extra_left=900000000)
    print(f"RESULT pass={len(PASS)} fail={len(FAIL)}")
    return 1 if FAIL else 0

async def act1():
    T = tokens(); A, S = T["absent"], T["scout"]
    insert_owner_msg("pack-main", "@absent 把桌面壁纸换成雪山")
    insert_owner_msg("pack-main", "@scout 统计本周出图数量")
    ta = await wait_task("把桌面壁纸换成雪山")
    tb = await wait_task("统计本周出图数量")
    check("watcher 两条 @ 均转卡", bool(ta) and bool(tb), f"A={ta and ta['id']} B={tb and tb['id']}")
    # absent: GUI 任务全程
    ok, cl = await call(A, "task_claim", task_id=ta["id"]); toka = cl["claim_token"]
    await call(A, "run_start", task_id=ta["id"], claim_token=toka)
    ok, acq = await call(A, "desktop_acquire", task_id=ta["id"], claim_token=toka)
    check("absent 拿桌面锁", ok and acq["held"])
    # scout: 无头任务并行交活（absent 持锁期间）
    ok, cl = await call(S, "task_claim", task_id=tb["id"]); tokb = cl["claim_token"]
    await call(S, "run_start", task_id=tb["id"], claim_token=tokb)
    env = {"verdict": "done", "summary": "本周出图 12 张", "artifacts": [],
           "self_check": "已按 delivery-envelope v2 核对", "known_issues": [], "followups": []}
    ok, _ = await call(S, "task_complete", task_id=tb["id"], claim_token=tokb,
                       result="done", envelope=env)
    ok, st = await call(D, "desktop_status")
    check("无头任务并行交活且不扰锁", ok and st["holder"] == "absent")
    ok, _ = await call(D, "task_review", task_id=tb["id"], approve=True)
    check("scout 任务验收关单", ok)
    # absent: 报进度 → 放锁 → 交活 → 验收
    await call(A, "task_update", task_id=ta["id"], claim_token=toka, note="壁纸已换，收尾中")
    ok, rel = await call(A, "desktop_release", task_id=ta["id"], claim_token=toka)
    check("absent 主动放锁", ok and rel["released"])
    env2 = {"verdict": "done", "summary": "壁纸已换成雪山", "artifacts": [],
            "self_check": "已按 delivery-envelope v2 核对：GUI 段先拿锁、段末放锁",
            "known_issues": [], "followups": []}
    ok, _ = await call(A, "task_complete", task_id=ta["id"], claim_token=toka,
                       result="done", envelope=env2)
    ok, st = await call(D, "desktop_status")
    check("交活后锁为空", ok and st["held"] is False)
    ok, _ = await call(D, "task_review", task_id=ta["id"], approve=True)
    check("absent 任务验收关单", ok)
    print(f"RESULT pass={len(PASS)} fail={len(FAIL)}")
    return 1 if FAIL else 0

async def act2():
    T = tokens(); A, S = T["absent"], T["scout"]
    # 模板直发（room-x）
    insert_owner_msg("room-x", "模板：出图 一只橙色的狐狸")
    tt = await wait_task("一只橙色的狐狸")
    check("模板在 room-x 开卡", bool(tt), f"id={tt and tt['id']}")
    ok, cl = await call(A, "task_claim", task_id=tt["id"])
    await call(A, "task_complete", task_id=tt["id"], claim_token=cl["claim_token"], result="图已出")
    await call(D, "task_review", task_id=tt["id"], approve=True)
    # 排班（真实钟 45 秒）
    at = time.time() + 45
    ok, sch = await call(D, "task_schedule", room="pack-main", title="明早巡检",
                         body="到点执行", member_name="absent", at_epoch=at)
    sid = sch["task_id"]
    ok, _ = await call(A, "task_claim", task_id=sid)
    check("排班到点前认领被拒", not ok)
    # 接力：absent 认领后额度阻塞 → 自动转 scout → scout 接手交活
    ok, rt = await call(D, "task_create", room="pack-main", title="整理素材库",
                        body="无头整理", assignee_name="absent")
    rid = rt["task_id"]
    ok, cl = await call(A, "task_claim", task_id=rid); tokr = cl["claim_token"]
    await call(S, "heartbeat")
    await call(D, "member_quota_set", member_name="scout", week_used_pct=10)
    ok, blk = await call(A, "task_block", task_id=rid, claim_token=tokr,
                         reason="额度耗尽", fail_class="quota")
    check("quota 阻塞触发接力到 scout", ok and blk.get("relayed_to") == "scout", str(blk)[:90])
    ok, acc = await call(S, "task_accept_transfer", task_id=rid)
    check("scout 接受转交领令牌", ok and bool(acc.get("claim_token")), str(acc)[:70])
    ok, _ = await call(S, "task_complete", task_id=rid, claim_token=acc["claim_token"], result="整理完")
    await call(D, "task_review", task_id=rid, approve=True)
    # 等排班到点并走完
    while time.time() < at + 3:
        await asyncio.sleep(3)
    ok, cl = await call(A, "task_claim", task_id=sid)
    check("排班到点认领成功", ok)
    await call(A, "task_complete", task_id=sid, claim_token=cl["claim_token"], result="巡检完")
    await call(D, "task_review", task_id=sid, approve=True)
    # 摘要卡
    s = {"decided": ["全流程总演练通过（staging26）"], "in_progress": [], "pending": []}
    ok, _ = await call(D, "room_summary_set", room="pack-main", summary=json.dumps(s, ensure_ascii=False))
    ok2, got = await call(D, "room_summary_get", room="pack-main")
    check("摘要卡更新可读", ok and ok2 and got["summary"]["decided"] == s["decided"])
    # 终局核账
    ok, out = await call(D, "task_list")
    states = {t["id"]: t["status"] for t in out["tasks"]}
    check("全部任务终局 done", all(v == "done" for v in states.values()), str(states))
    conn = sqlite3.connect(DB)
    integ = conn.execute("PRAGMA integrity_check").fetchone()[0]
    acts = [r[0] for r in conn.execute("SELECT DISTINCT action FROM audit").fetchall()]
    conn.close()
    check("DB integrity ok", integ == "ok")
    for want in ("task_claim", "desktop_acquire", "desktop_release",
                 "task_block", "task_auto_relay", "task_accept_transfer", "task_complete", "task_review"):
        check(f"审计含 {want}", want in acts)
    print(f"RESULT pass={len(PASS)} fail={len(FAIL)}")
    return 1 if FAIL else 0

if __name__ == "__main__":
    phase = sys.argv[1]
    fn = {"seed": seed, "act1": act1, "act2": act2}[phase]
    sys.exit(asyncio.run(fn()))
