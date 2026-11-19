"""staging19 专测：额度排班 task_schedule + 自动接力 _auto_relay。"""
import asyncio, json, sqlite3, subprocess, sys, time
sys.path.insert(0, "/home/hatch/workspace/pack/hub-staging19")
from harness import call, setup_members

DB = "/home/hatch/workspace/pack/hub-staging19/pack.db"
PASS = FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ok {name}")
    else: FAIL += 1; print(f"  FAIL {name} {extra}")

def dbx(q, args=()):
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    rows = c.execute(q, args).fetchall(); c.commit(); c.close()
    return rows

async def main():
    toks = await setup_members()  # alice / bob / owner
    D, A, B, O = toks["dispatcher"], toks["alice"], toks["bob"], toks["owner"]
    now = time.time()

    print("== 排班 ==")
    ok, out = await call(D, "task_schedule", room="pack-main", title="排班卡",
                         body="等额度", member_name="alice", at_epoch=now + 3600)
    check("schedule ok", ok and out["assignee"] == "alice", str(out))
    sid = out["task_id"]
    ok, out = await call(D, "task_list")
    t = [x for x in out["tasks"] if x["id"] == sid][0]
    check("sched_at visible in list", t["sched_at"] is not None and abs(t["sched_at"] - (now + 3600)) < 5, str(t))
    ok, out = await call(A, "task_claim", task_id=sid)
    check("claim before time rejected", not ok, str(out)[:100])
    ok, out = await call(D, "task_schedule", room="pack-main", title="x", body="b",
                         member_name="alice", at_epoch=now - 10)
    check("past time rejected", not ok)
    ok, out = await call(D, "task_schedule", room="pack-main", title="x", body="b",
                         member_name="ghost", at_epoch=now + 60)
    check("ghost member rejected", not ok)
    ok, out = await call(D, "task_schedule", room="pack-main", title="x", body="b",
                         member_name="owner", at_epoch=now + 60)
    check("owner target rejected", not ok)
    ok, out = await call(A, "task_schedule", room="pack-main", title="x", body="b",
                         member_name="alice", at_epoch=now + 60)
    check("member cannot schedule", not ok)
    ok, out = await call(D, "task_clear_backoff", task_id=sid)
    check("clear_backoff cancels schedule", ok, str(out))
    ok, out = await call(D, "task_list")
    t = [x for x in out["tasks"] if x["id"] == sid][0]
    check("sched_at cleared", t["sched_at"] is None, str(t))
    ok, out = await call(A, "task_claim", task_id=sid)
    check("claimable after cancel", ok and out.get("claim_token"), str(out))
    alice_tok = out["claim_token"]
    ok, out = await call(A, "task_block", task_id=sid, claim_token=alice_tok,
                         reason="收尾", fail_class="task")
    check("cleanup block", ok and out["relayed_to"] is None, str(out))

    ok, out = await call(D, "task_schedule", room="pack-main", title="短排班",
                         body="b", member_name="alice", at_epoch=time.time() + 2)
    sid2 = out["task_id"]
    await asyncio.sleep(3)
    ok, out = await call(A, "task_claim", task_id=sid2)
    check("claim after scheduled time ok", ok, str(out))
    ok, out = await call(D, "task_list")
    t = [x for x in out["tasks"] if x["id"] == sid2][0]
    check("sched_at cleared on claim", t["sched_at"] is None, str(t))

    print("== 接力：quota 阻塞 ==")
    # bob 合格候选：心跳新鲜 + 额度新鲜且有余量
    await call(B, "heartbeat")
    ok, out = await call(D, "member_quota_set", member_name="bob",
                         week_used_pct=20.0, week_reset_at=now + 86400, extra_tokens_left=1000)
    check("bob quota seeded", ok, str(out))
    ok, out = await call(D, "task_create", room="pack-main", title="接力卡一号",
                         body="b", assignee_name="alice")
    tid = out["task_id"]
    ok, out = await call(A, "task_claim", task_id=tid)
    atok = out["claim_token"]
    ok, out = await call(A, "task_block", task_id=tid, claim_token=atok,
                         reason="额度用完了", fail_class="quota")
    check("quota block relays to bob", ok and out["relayed_to"] == "bob" and out["status"] == "claimed", str(out))
    rows = dbx("SELECT * FROM task_transfers WHERE task_id=?", (tid,))
    check("transfer row pending by auto-relay",
          len(rows) == 1 and rows[0]["status"] == "pending" and rows[0]["actor"] == "auto-relay"
          and rows[0]["to_member"] == "bob", str([dict(r) for r in rows]))
    ok, out = await call(B, "task_accept_transfer", task_id=tid)
    check("bob accepts relay", ok and out.get("claim_token"), str(out))
    btok = out["claim_token"]
    ok, out = await call(B, "task_complete", task_id=tid, claim_token=btok,
                         result="接力完成",
                         envelope={"verdict": "done", "summary": "接力后干完"})
    check("bob completes relayed task", ok, str(out))

    print("== 接力：不合格候选 ==")
    # bob 额度数据过期 → 不合格
    dbx("UPDATE members SET quota_updated_at=? WHERE name='bob'", (time.time() - 2000,))
    ok, out = await call(D, "task_create", room="pack-main", title="接力卡二号",
                         body="b", assignee_name="alice")
    tid2 = out["task_id"]
    ok, out = await call(A, "task_claim", task_id=tid2)
    ok, out = await call(A, "task_block", task_id=tid2, claim_token=out["claim_token"],
                         reason="又没额度", fail_class="quota")
    check("stale quota candidate -> no relay, stays blocked",
          ok and out["relayed_to"] is None and out["status"] == "blocked", str(out))
    # bob 额度恢复新鲜但已用 95% → 不合格
    await call(D, "member_quota_set", member_name="bob", week_used_pct=95.0)
    ok, out = await call(D, "task_create", room="pack-main", title="接力卡三号",
                         body="b", assignee_name="alice")
    tid3 = out["task_id"]
    ok, out = await call(A, "task_claim", task_id=tid3)
    ok, out = await call(A, "task_block", task_id=tid3, claim_token=out["claim_token"],
                         reason="没额度", fail_class="quota")
    check("quota >=90 candidate -> no relay", ok and out["relayed_to"] is None, str(out))
    # 非 quota 阻塞不接力
    await call(D, "member_quota_set", member_name="bob", week_used_pct=10.0)
    ok, out = await call(D, "task_create", room="pack-main", title="普通阻塞卡",
                         body="b", assignee_name="alice")
    tid4 = out["task_id"]
    ok, out = await call(A, "task_claim", task_id=tid4)
    ok, out = await call(A, "task_block", task_id=tid4, claim_token=out["claim_token"],
                         reason="输入缺失", fail_class="task")
    check("non-quota block -> no relay", ok and out["relayed_to"] is None and out["status"] == "blocked", str(out))
    # 带执行体标记的任务不接力
    ok, out = await call(D, "task_create", room="pack-main", title="[ENG] 标记卡",
                         body="b", assignee_name="alice")
    tid5 = out["task_id"]
    ok, out = await call(A, "task_claim", task_id=tid5)
    ok, out = await call(A, "task_block", task_id=tid5, claim_token=out["claim_token"],
                         reason="没额度", fail_class="quota")
    check("executor-marked task -> no relay", ok and out["relayed_to"] is None, str(out))

    print("== 接力：sweeper 掉线路径 ==")
    ok, out = await call(D, "task_create", room="pack-main", title="掉线卡",
                         body="b", assignee_name="alice")
    tid6 = out["task_id"]
    ok, out = await call(A, "task_claim", task_id=tid6)
    check("alice claimed drop task", ok, str(out))
    dbx("UPDATE tasks SET lease_expires=? WHERE id=?", (time.time() - 10, tid6))
    r = subprocess.run([sys.executable, "-c",
                        "import sys; sys.path.insert(0, '/home/hatch/workspace/pack/hub-staging19');"
                        "import hub; print(hub.sweep_once())"],
                       capture_output=True, text=True, cwd="/home/hatch/workspace/pack/hub-staging19")
    check("sweep ran", r.returncode == 0, r.stderr[-200:])
    rows = dbx("SELECT * FROM task_transfers WHERE task_id=? AND actor='auto-relay'", (tid6,))
    trows = dbx("SELECT status, assignee FROM tasks WHERE id=?", (tid6,))
    check("sweeper relayed dropped task to bob",
          len(rows) == 1 and rows[0]["to_member"] == "bob" and trows[0]["status"] == "claimed",
          str([dict(x) for x in rows]) + str([dict(x) for x in trows]))
    ok, out = await call(B, "task_accept_transfer", task_id=tid6)
    check("bob accepts sweeper-relayed task", ok and out.get("claim_token"), str(out))

    print(f"RESULT pass={PASS} fail={FAIL}")
    return FAIL

if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
