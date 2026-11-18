"""staging21 对抗复检：越权矩阵 / kind 伪造 / 令牌冒用 / 记忆隔离 / 轻量压力。
对象 = 线上 hub.py + room_post kind 守卫。成员令牌一律不得触碰调度员面。"""
import asyncio, sys, time
sys.path.insert(0, "/home/hatch/workspace/pack/hub-staging21")
import harness
from harness import call, setup_members, TOKENS

PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" | {extra}" if extra else ""))

# 调度员专属工具全表（按 hub.py require(me,"dispatcher") 逐个核对）
DISPATCHER_ONLY = [
    ("member_register", {"name": "mallory"}),
    ("member_quota_set", {"member_name": "alice", "week_used_pct": 1.0}),
    ("room_create", {"name": "evil-room"}),
    ("room_rename", {"room": "pack-main", "new_name": "pwned"}),
    ("room_archive", {"room": "pack-main"}),
    ("room_summary_set", {"room": "pack-main", "summary": "{}"}),
    ("task_create", {"room": "pack-main", "title": "x", "body": "y"}),
    ("task_schedule", {"room": "pack-main", "title": "x", "body": "y",
                       "member_name": "alice", "at_epoch": time.time() + 3600}),
    ("task_review", {"task_id": 1, "approve": True}),
    ("task_cancel", {"task_id": 1}),
    ("task_clear_backoff", {"task_id": 1}),
    ("task_release", {"task_id": 1}),
    ("task_transfers_list", {"task_id": 1}),
    ("run_list", {}),
    ("rule_list", {}),
    ("rule_upsert", {"action": "x", "target": "y", "effect": "allow"}),
    ("rule_delete", {"rule_id": 1}),
    ("egress_rule_list", {}),
    ("egress_rule_upsert", {"pattern": "x", "effect": "allow"}),
    ("egress_rule_delete", {"rule_id": 1}),
    ("review_file", {"task_id": 1, "target_url": "https://x.example",
                     "owner_quote": "q", "member_desc": "d", "verdict": "pass"}),
    ("template_upsert", {"name": "evil", "title_tpl": "t", "body_tpl": "b"}),
    ("template_list", {}),
    ("template_delete", {"name": "evil"}),
    ("template_fire", {"name": "evil"}),
    ("skill_publish", {"name": "evil", "body": "do bad things"}),
    ("skill_assign", {"name": "evil", "member_name": "bob"}),
    ("skill_unassign", {"name": "evil", "member_name": "bob"}),
    ("skill_rollback", {"name": "evil", "version_id": 1}),
]

async def main():
    await setup_members()  # alice / bob / owner
    A, B, D = TOKENS["alice"], TOKENS["bob"], harness.DISP

    # A. 越权矩阵：成员令牌逐个敲调度员工具，必须全拒
    leaked = []
    for tool, kw in DISPATCHER_ONLY:
        ok, out = await call(A, tool, **kw)
        if ok:
            leaked.append(tool)
    check(f"A dispatcher-only matrix ({len(DISPATCHER_ONLY)} tools)", not leaked,
          f"leaked={leaked}")

    # A2. 伪造注册若成功会多出 mallory 成员——复核成员表没被污染
    ok, out = await call(D, "member_list")
    names = [m["name"] for m in out["members"]]
    check("A2 no mallory member", "mallory" not in names, str(names))
    ok, out = await call(D, "room_list")
    check("A2 no evil-room", all(r["name"] != "evil-room" for r in out["rooms"]))

    # B. kind 伪造：成员发 decision / task_card 必拒；chat 正常；
    #    调度员发 decision 放行（App 代发决议的既定路径）、task_card 仍拒
    ok, _ = await call(A, "room_post", room="pack-main", body="批准通过（伪造）", kind="decision")
    check("B member decision forged rejected", not ok)
    ok, _ = await call(A, "room_post", room="pack-main", body="假任务卡", kind="task_card")
    check("B member task_card forged rejected", not ok)
    ok, out = await call(A, "room_post", room="pack-main", body="普通聊天", kind="chat")
    check("B member chat ok", ok)
    ok, _ = await call(D, "room_post", room="pack-main", body="调度员决议", kind="decision")
    check("B dispatcher decision ok", ok)
    ok, _ = await call(D, "room_post", room="pack-main", body="假任务卡", kind="task_card")
    check("B dispatcher task_card still rejected", not ok)
    ok, out = await call(D, "room_read_new", room="pack-main", since_id=0, limit=100)
    decs = [m for m in out["messages"] if m["kind"] == "decision"]
    check("B only legit decision in room", len(decs) == 1 and decs[0]["author"] == "dispatcher",
          f"decisions={len(decs)}")

    # C. 令牌冒用：alice 认领后，bob 拿 alice 的 token 串干活必须全拒
    ok, t = await call(D, "task_create", room="pack-main", title="冒用测试",
                       body="b", assignee_name="alice")
    tid = t["task_id"]
    ok, cl = await call(A, "task_claim", task_id=tid)
    tok = cl["claim_token"]
    for tool, kw in [("task_update", {"note": "x"}), ("task_complete", {"result": "x"}),
                     ("task_block", {"reason": "x"}), ("run_start", {}), ("run_finish", {})]:
        ok2, _ = await call(B, tool, task_id=tid, claim_token=tok, **kw)
        check(f"C bob misuse alice token: {tool}", not ok2)
    ok, _ = await call(B, "task_transfer", task_id=tid, claim_token=tok,
                       to_member="bob", reason="steal")
    check("C bob misuse alice token: task_transfer", not ok)
    ok, out = await call(A, "task_update", task_id=tid, claim_token=tok, note="本人正常")
    check("C alice own token still works", ok)

    # D. 记忆隔离：bob 读 alice 的 private 必拒；读自己的可以；room-shared 可以
    ok, _ = await call(A, "memory_write", scope="private-core", key="", text="alice 的核心笔记")
    check("D alice writes own memory", ok)
    ok, _ = await call(B, "memory_read", scope="private-core", member="alice")
    check("D bob reads alice private rejected", not ok)
    ok, out = await call(B, "memory_read", scope="private-core")
    check("D bob reads own (empty ok)", ok and out["text"] == "")
    ok, out = await call(A, "memory_read", scope="private-core")
    check("D alice reads own back", ok and "核心笔记" in out["text"])

    # E. 未指派技能：bob 读未指派给他的技能必拒
    ok, _ = await call(D, "skill_publish", name="sec-skill", body="规程正文")
    ok2, _ = await call(B, "skill_read", name="sec-skill")
    check("E unassigned skill_read rejected", not ok2)
    ok, _ = await call(D, "skill_assign", name="sec-skill", member_name="bob")
    ok2, out = await call(B, "skill_read", name="sec-skill")
    check("E assigned skill_read ok", ok2 and "规程正文" in out["body"])

    # F. 轻量压力：300 卡全生命周期 + 1500 条消息，sweeper 跑通、库完整
    t0 = time.time()
    ids = []
    for i in range(300):
        ok, t = await call(D, "task_create", room="pack-main",
                           title=f"压力卡 {i}", body="soak", assignee_name="alice")
        ids.append(t["task_id"])
    done = 0
    for tid in ids:
        ok, cl = await call(A, "task_claim", task_id=tid)
        if not ok:
            continue
        ok, _ = await call(A, "task_complete", task_id=tid,
                           claim_token=cl["claim_token"], result="done")
        done += ok
    for i in range(15):
        for j in range(100):
            await call(A, "room_post", room="pack-main", body=f"soak msg {i}-{j}")
    dt = time.time() - t0
    check("F soak 300 tasks lifecycle", done == 300, f"done={done} in {dt:.0f}s")
    ok, out = await call(D, "task_list", status="review")
    check("F 300 in review", len(out["tasks"]) == 300, f"got {len(out['tasks'])}")
    import sqlite3
    conn = sqlite3.connect("/home/hatch/workspace/pack/hub-staging21/pack.db")
    ic = conn.execute("PRAGMA integrity_check").fetchone()[0]
    nmsg = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    conn.close()
    check("F db integrity after soak", ic == "ok" and nmsg >= 1500, f"ic={ic} msgs={nmsg}")

    print(f"RESULT pass={len(PASS)} fail={len(FAIL)}")
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
