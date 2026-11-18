"""staging14 复检·补充：正向对照（原子性本就达标处）/ 剩余功能点 / FD 泄漏抽测 /
sweeper 语句级交错 / egress owner 点名（SQL 造数 + API 判定）。"""
import asyncio, glob, json, os, sqlite3, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import call, client, DISP

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "pack.db")
T = json.load(open(os.path.join(BASE, "tokens.json")))
A, B, D = T["alice"], T["bob"], T["dispatcher"]
OUT = []
def log(s): OUT.append(s); print(s)
def sq(q, args=()):
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    rows = c.execute(q, args).fetchall(); c.close(); return rows

async def main():
    # ---- 正向对照 1：task_claim 双抢（原子 UPDATE 应恰好一个赢家）----
    wins = 0
    for rnd in range(6):
        ok, t = await call(D, "task_create", room="pack-main", title=f"双抢 {rnd}",
                           body="b")
        tid = t["task_id"]
        r = await asyncio.gather(call(A, "task_claim", task_id=tid),
                                 call(B, "task_claim", task_id=tid))
        n = sum(1 for ok, _ in r if ok)
        wins += (n == 1)
        log(f"PC1 round{rnd}: 双抢成功数 {n}（期望 1）")
    log(f"PC1 RESULT: {'原子认领达标' if wins == 6 else '异常'} {wins}/6")

    # ---- 正向对照 2：room_create 重名并发（UNIQUE 兜底）----
    r = await asyncio.gather(*[call(D, "room_create", name="race-room") for _ in range(4)])
    n = sum(1 for ok, _ in r if ok)
    errs = [str(e) for ok, e in r if not ok]
    log(f"PC2 RESULT: 并发建同名房成功 {n}（期望 1），其余错误均为 already exists: "
        f"{all('already exists' in e for e in errs)}")

    # ---- 正向对照 3：run_start 双登记（fence 应拒第二个）----
    ok, t = await call(D, "task_create", room="pack-main", title="fence 对照",
                       body="b", assignee_name="alice")
    tid = t["task_id"]
    ok, claim = await call(A, "task_claim", task_id=tid)
    tok = claim["claim_token"]
    r = await asyncio.gather(call(A, "run_start", task_id=tid, claim_token=tok),
                             call(A, "run_start", task_id=tid, claim_token=tok))
    n = sum(1 for ok, _ in r if ok)
    naud = len(sq("SELECT id FROM audit WHERE action='run_fence_reject' AND detail LIKE ?",
                  (f"task {tid}%",)))
    log(f"PC3 RESULT: 双 run_start 成功 {n}（期望 1），fence_reject 审计 {naud}（期望 ≥1）")
    ok, out = await call(A, "run_finish", task_id=tid, claim_token=tok, outcome="aborted")
    log(f"PC3b run_finish: {ok}")
    ok, e = await call(A, "run_finish", task_id=tid, claim_token=tok, outcome="bogus")
    log(f"PC3c run_finish 非法 outcome 被拒: {not ok}")

    # ---- 转交接力上限：3 次后第 4 次拒绝 + 审计 + 房间消息 ----
    ok, t = await call(D, "task_create", room="pack-main", title="接力上限",
                       body="b", assignee_name="alice")
    tid = t["task_id"]
    ok, claim = await call(A, "task_claim", task_id=tid)
    cur_tok, holder, other = claim["claim_token"], A, B
    for i in range(3):
        ok, out = await call(holder, "task_transfer", task_id=tid,
                             claim_token=cur_tok, to_member="bob" if holder is A else "alice")
        assert ok, out
        ok, acc = await call(other, "task_accept_transfer", task_id=tid)
        assert ok, acc
        cur_tok = acc["claim_token"]; holder, other = other, holder
    ok, e = await call(holder, "task_transfer", task_id=tid, claim_token=cur_tok,
                       to_member="bob" if holder is A else "alice")
    naud = len(sq("SELECT id FROM audit WHERE action='transfer_cap_reject' AND detail LIKE ?",
                  (f"task {tid}%",)))
    nmsg = len(sq("SELECT id FROM messages WHERE task_id=? AND body LIKE '%接力上限%'", (tid,)))
    log(f"CAP RESULT: 第 4 次转交被拒={not ok}（{str(e)[:60]}），cap 审计 {naud}，房间提示 {nmsg}")

    # ---- task_review 打回重算退避 ----
    ok, t = await call(D, "task_create", room="pack-main", title="打回退避",
                       body="b", assignee_name="alice")
    tid = t["task_id"]
    ok, claim = await call(A, "task_claim", task_id=tid)
    await call(A, "task_complete", task_id=tid, claim_token=claim["claim_token"], result="r")
    ok, out = await call(D, "task_review", task_id=tid, approve=False, note="重做")
    row = sq("SELECT status, next_claim_at FROM tasks WHERE id=?", (tid,))[0]
    delta = (row["next_claim_at"] or 0) - time.time()
    log(f"REVIEW-REJ RESULT: 打回后 status={row['status']}，退避 {delta:.0f}s"
        f"（unknown 基数 300×2^0，期望 ≈300）")

    # ---- egress owner 点名（SQL 造 created_by=owner 的任务，API 判定）----
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    owner_id = c.execute("SELECT id FROM members WHERE name='owner'").fetchone()["id"]
    alice_id = c.execute("SELECT id FROM members WHERE name='alice'").fetchone()["id"]
    room_id = c.execute("SELECT id FROM rooms WHERE name='pack-main'").fetchone()["id"]
    cur = c.execute("INSERT INTO tasks(room_id,title,body,created_by,status,created_at,updated_at) "
                    "VALUES(?, 'owner任务', '见 https://owner-named.example.com/x', ?, 'open', 0, 0)",
                    (room_id, owner_id)); t_own = cur.lastrowid
    cur = c.execute("INSERT INTO tasks(room_id,title,body,created_by,status,created_at,updated_at) "
                    "VALUES(?, 'alice任务', '见 https://member-named.example.com/x', ?, 'open', 0, 0)",
                    (room_id, alice_id)); t_mem = cur.lastrowid
    c.commit(); c.close()
    ok, out = await call(B, "egress_check", url="https://owner-named.example.com/y", task_id=t_own)
    log(f"EG-OWNER RESULT: owner（role=member）建的任务点名 -> {out.get('decision')}（期望 allow）")
    ok, out = await call(B, "egress_check", url="https://member-named.example.com/y", task_id=t_mem)
    log(f"EG-MEM RESULT: 普通成员建的任务点名 -> {out.get('decision')}（期望 ask，防自授）")

    # ---- FD 泄漏抽测：120 次失败调用前后数 server 的 fd ----
    pid = int(open("/proc/net/tcp").read() and os.popen(
        "ss -ltnp | grep 8814 | grep -oP 'pid=\\K[0-9]+' | head -1").read().strip())
    def nfds(): return len(glob.glob(f"/proc/{pid}/fd/*"))
    n0 = nfds()
    for i in range(40):
        await call("bad-token", "task_list")
        await call(A, "task_complete", task_id=1, claim_token="nope", result="x")
        await call(B, "memory_read", scope="private-core", member="alice")
    n1 = nfds()
    log(f"FD RESULT: server pid={pid} 失败调用 120 次 fd {n0} -> {n1}"
        f"（{'无增长' if n1 <= n0 + 2 else '疑似泄漏'}）")

asyncio.run(main())

# ---- SW1 语句级：sweeper 过期转交 UPDATE 无 status 守卫（源码 L691 逐字）----
c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
alice = c.execute("SELECT id FROM members WHERE name='alice'").fetchone()["id"]
bob = c.execute("SELECT id FROM members WHERE name='bob'").fetchone()["id"]
room = c.execute("SELECT id FROM rooms WHERE name='pack-main'").fetchone()["id"]
cur = c.execute("INSERT INTO tasks(room_id,title,body,created_by,assignee,status,claim_token,"
                "lease_expires,attempts,created_at,updated_at) VALUES(?, 'SW1', 'b', 1, ?, "
                "'claimed', NULL, NULL, 1, 0, 0)", (room, bob)); tid = cur.lastrowid
cur = c.execute("INSERT INTO task_transfers(task_id,from_member,to_member,actor,created_at,"
                "status,expires_at) VALUES(?, 'alice', 'bob', 'alice', 0, 'pending', 1)",
                (tid,)); xid = cur.lastrowid
c.commit()
# sweeper 先 SELECT 到这条 pending（已过期）
sel = c.execute("SELECT * FROM task_transfers WHERE status='pending' AND expires_at IS NOT NULL "
                "AND expires_at<?", (time.time(),)).fetchall()
assert any(x["id"] == xid for x in sel)
# 接收方此刻 accept 成功提交（与 task_accept_transfer 的两条 UPDATE 同语义）
c.execute("UPDATE tasks SET claim_token='TOK_NEW', lease_expires=9999999999 WHERE id=?", (tid,))
c.execute("UPDATE task_transfers SET status='accepted', accepted_at=? WHERE id=?",
          (time.time(), xid)); c.commit()
# sweeper 接着执行 L691 逐字 UPDATE（无 status 守卫）
c.execute("UPDATE task_transfers SET status='expired' WHERE id=?", (xid,)); c.commit()
x = c.execute("SELECT status, accepted_at FROM task_transfers WHERE id=?", (xid,)).fetchone()
c.close()
log(f"SW1 RESULT: accept 已提交后，sweeper 无守卫 UPDATE 把转交行改成 status={x['status']}"
    f"（accepted_at 仍在={x['accepted_at'] is not None}）=> 已接受被改标过期，账面自相矛盾")

# ---- SW2 语句级：sweeper 租约回收 UPDATE 无 lease 守卫（源码 L717 区块逐字语义）----
c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
cur = c.execute("INSERT INTO tasks(room_id,title,body,created_by,assignee,status,claim_token,"
                "lease_expires,attempts,created_at,updated_at) VALUES(?, 'SW2', 'b', 1, ?, "
                "'working', 'TOK_H', ?, 1, 0, 0)", (room, alice, time.time() - 1)); tid2 = cur.lastrowid
c.commit()
rows = c.execute("SELECT * FROM tasks WHERE status IN ('claimed','working') AND "
                 "lease_expires IS NOT NULL AND lease_expires<?", (time.time(),)).fetchall()
assert any(r["id"] == tid2 for r in rows)  # sweeper 选中
# 持有人此刻 task_update 续租成功（源码 UPDATE 语义）
c.execute("UPDATE tasks SET status='working', lease_expires=?, updated_at=0 WHERE id=?",
          (time.time() + 1800, tid2)); c.commit()
# sweeper 按先前 SELECT 的行执行回收 UPDATE（WHERE 只有 id，无 lease 复检）
c.execute("UPDATE tasks SET status='open', assignee=NULL, claim_token=NULL, lease_expires=NULL, "
          "fail_class='transient', next_claim_at=?, updated_at=0 WHERE id=?",
          (time.time() + 60, tid2)); c.commit()
row = c.execute("SELECT status, claim_token FROM tasks WHERE id=?", (tid2,)).fetchone()
c.close()
log(f"SW2 RESULT: 持有人已续租成功后，sweeper 仍按旧快照回收：status={row['status']} "
    f"claim_token={row['claim_token']} => 活跃任务被误回收回池")

open(os.path.join(BASE, "more-output.txt"), "w").write("\n".join(OUT) + "\n")
print("saved more-output.txt")
