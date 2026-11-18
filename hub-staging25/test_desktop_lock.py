"""staging25 桌面锁专测：获取/释放/状态、占用拒绝载荷、幂等、
权限与令牌校验、调度员强放、五条自动释放出口（complete/block/
cancel/transfer/租约回收）、并行抢锁竞态（3 轮）。"""
import asyncio, json, sqlite3, subprocess, sys, time
sys.path.insert(0, "/home/hatch/workspace/pack/hub-staging25")
import harness
from harness import call

DB = "/home/hatch/workspace/pack/hub-staging25/pack.db"
D = harness.DISP
PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" | {extra}" if extra else ""))

async def mk_task(assignee=None):
    ok, t = await call(D, "task_create", room="pack-main", title="锁演练",
                       body="desktop lock drill", assignee_name=assignee or "")
    return t["task_id"]

async def main():
    ok, r1 = await call(D, "member_register", name="alice")
    ok, r2 = await call(D, "member_register", name="bob")
    A, B = r1["token"], r2["token"]

    ok, st = await call(A, "desktop_status")
    check("status free at start", ok and st["held"] is False, str(st))

    t1 = await mk_task("alice")
    ok, cl = await call(A, "task_claim", task_id=t1)
    tok1 = cl["claim_token"]

    ok, acq = await call(A, "desktop_acquire", task_id=t1, claim_token=tok1)
    check("alice acquires", ok and acq["held"], str(acq)[:70])
    at1 = acq["acquired_at"]
    ok, acq2 = await call(A, "desktop_acquire", task_id=t1, claim_token=tok1)
    check("re-acquire idempotent", ok and acq2.get("idempotent") and acq2["acquired_at"] == at1)
    ok, st = await call(B, "desktop_status")
    check("status shows alice/t1", ok and st["holder"] == "alice" and st["task_id"] == t1)

    # bob 的任务抢锁 → busy 载荷
    t2 = await mk_task("bob")
    ok, cl2 = await call(B, "task_claim", task_id=t2)
    tok2 = cl2["claim_token"]
    ok, busy = await call(B, "desktop_acquire", task_id=t2, claim_token=tok2)
    check("bob acquire busy", not ok and "desktop_busy" in str(busy)
          and '"holder": "alice"' in str(busy), str(busy)[:110])

    # 校验类拒绝
    ok, _ = await call(A, "desktop_acquire", task_id=t1, claim_token="bogus")
    check("acquire wrong token refused", not ok)
    ok, _ = await call(B, "desktop_acquire", task_id=t1, claim_token=tok1)
    check("acquire other's task refused", not ok)
    ok, _ = await call(D, "desktop_acquire", task_id=t1, claim_token=tok1)
    check("dispatcher acquire refused", not ok)
    ok, _ = await call(B, "desktop_release", task_id=t2, claim_token=tok2)
    check("release by non-holder task refused", not ok)
    ok, _ = await call(A, "desktop_release", task_id=t1, claim_token="bogus")
    check("release wrong token refused", not ok)
    ok, st = await call(A, "desktop_status")
    check("lock still alice after refused releases", st["holder"] == "alice")

    # 调度员强放
    ok, rel = await call(D, "desktop_release")
    check("dispatcher force release", ok and rel["released"] and rel["was_task_id"] == t1, str(rel))
    ok, st = await call(A, "desktop_status")
    check("free after force release", st["held"] is False)
    ok, rel = await call(D, "desktop_release")
    check("release when free is no-op", ok and rel["released"] is False)

    # bob 现在能拿；正常放
    ok, acq = await call(B, "desktop_acquire", task_id=t2, claim_token=tok2)
    check("bob acquires after force release", ok and acq["held"])
    ok, rel = await call(B, "desktop_release", task_id=t2, claim_token=tok2)
    check("bob releases own", ok and rel["released"])
    await call(B, "task_cancel", task_id=t2)  # dispatcher 视角清理须用 D
    ok, _ = await call(D, "task_cancel", task_id=t2)
    ok, _ = await call(A, "task_update", task_id=t1, claim_token=tok1, note="")

    # 自动释放 1：complete
    ok, _ = await call(A, "desktop_acquire", task_id=t1, claim_token=tok1)
    ok, _ = await call(A, "task_complete", task_id=t1, claim_token=tok1, result="done")
    ok, st = await call(A, "desktop_status")
    check("auto-release on complete", ok and st["held"] is False)
    await call(D, "task_review", task_id=t1, approve=True)

    # 自动释放 2：block
    t3 = await mk_task("alice")
    ok, cl3 = await call(A, "task_claim", task_id=t3)
    await call(A, "desktop_acquire", task_id=t3, claim_token=cl3["claim_token"])
    ok, _ = await call(A, "task_block", task_id=t3, claim_token=cl3["claim_token"], reason="stuck")
    ok, st = await call(A, "desktop_status")
    check("auto-release on block", ok and st["held"] is False)
    await call(D, "task_cancel", task_id=t3)

    # 自动释放 3：cancel（调度员取消持锁中的任务）
    t4 = await mk_task("alice")
    ok, cl4 = await call(A, "task_claim", task_id=t4)
    await call(A, "desktop_acquire", task_id=t4, claim_token=cl4["claim_token"])
    ok, _ = await call(D, "task_cancel", task_id=t4)
    ok, st = await call(A, "desktop_status")
    check("auto-release on cancel", ok and st["held"] is False)

    # 自动释放 4：transfer
    t5 = await mk_task("alice")
    ok, cl5 = await call(A, "task_claim", task_id=t5)
    await call(A, "desktop_acquire", task_id=t5, claim_token=cl5["claim_token"])
    ok, _ = await call(A, "task_transfer", task_id=t5, claim_token=cl5["claim_token"], to_member="bob")
    ok, st = await call(A, "desktop_status")
    check("auto-release on transfer", ok and st["held"] is False)
    await call(D, "task_cancel", task_id=t5)

    # 自动释放 5：租约到期 sweeper 回收
    t6 = await mk_task("alice")
    ok, cl6 = await call(A, "task_claim", task_id=t6)
    await call(A, "desktop_acquire", task_id=t6, claim_token=cl6["claim_token"])
    conn = sqlite3.connect(DB)
    conn.execute("UPDATE tasks SET lease_expires=? WHERE id=?", (time.time() - 10, t6))
    conn.commit(); conn.close()
    subprocess.run([sys.executable, "hub.py", "sweep"],
                   cwd="/home/hatch/workspace/pack/hub-staging25",
                   capture_output=True, timeout=60)
    ok, st = await call(A, "desktop_status")
    check("auto-release on lease reap", ok and st["held"] is False)
    ok, lst = await call(D, "task_list")
    t6row = [x for x in lst["tasks"] if x["id"] == t6][0]
    check("reaped task back open", t6row["status"] == "open", t6row["status"])

    # 并行抢锁竞态 ×3 轮：恰好一个赢家
    for rnd in range(3):
        ta = await mk_task("alice"); tb = await mk_task("bob")
        ok, ca = await call(A, "task_claim", task_id=ta)
        ok, cb = await call(B, "task_claim", task_id=tb)
        res = await asyncio.gather(
            call(A, "desktop_acquire", task_id=ta, claim_token=ca["claim_token"]),
            call(B, "desktop_acquire", task_id=tb, claim_token=cb["claim_token"]))
        wins = [r for r in res if r[0] and r[1].get("held")]
        check(f"race round {rnd+1}: exactly one winner", len(wins) == 1, f"wins={len(wins)}")
        await call(D, "desktop_release")
        await call(D, "task_cancel", task_id=ta)
        await call(D, "task_cancel", task_id=tb)

    # 审计留痕抽查
    conn = sqlite3.connect(DB)
    acts = [r[0] for r in conn.execute(
        "SELECT action FROM audit WHERE action LIKE 'desktop%'").fetchall()]
    conn.close()
    for want in ("desktop_acquire", "desktop_release", "desktop_force_release", "desktop_auto_release"):
        check(f"audit has {want}", want in acts)

    print(f"RESULT pass={len(PASS)} fail={len(FAIL)}")
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
