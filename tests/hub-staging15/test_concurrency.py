"""staging14 复检·并发面：竞态实测（live）+ 语句级交错复现（确定性证据）。
live 竞态每项多轮重复；语句级用源码逐字语句 + barrier 交错，证明缺守卫。"""
import asyncio, json, os, sqlite3, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import call, client, DISP

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "pack.db")
T = json.load(open(os.path.join(BASE, "tokens.json")))
A, B, D = T["alice"], T["bob"], T["dispatcher"]
OUT = []

def log(s):
    OUT.append(s); print(s)

def sq(q, args=()):
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    rows = c.execute(q, args).fetchall(); c.close()
    return rows

def sqx(q, args=()):
    c = sqlite3.connect(DB); c.execute(q, args); c.commit(); c.close()

async def reg(name):
    async with client(DISP) as c:
        r = await c.call_tool("member_register", {"name": name})
        return json.loads(r.content[0].text)["token"]

# ---------- C0: 服务端是否真并发执行工具 ----------
async def c0():
    tok = await reg("timer1")
    big = "z" * 19999
    t0 = time.time()
    await call(tok, "memory_write", scope="private", key="solo", text=big)
    t1 = time.time() - t0
    t0 = time.time()
    await asyncio.gather(*[call(tok, "memory_write", scope="private",
                                key=f"cc{i}", text=big) for i in range(4)])
    t4 = time.time() - t0
    log(f"C0 timing: 1x={t1:.2f}s 4x并发={t4:.2f}s -> "
        f"{'并发执行' if t4 < t1 * 2.5 else '疑似串行'}")

# ---------- C1: 记忆主题数上限竞态（live） ----------
async def c1():
    hits = 0
    for rnd in range(6):
        name = f"racer{rnd}"
        tok = await reg(name)
        mid = sq("SELECT id FROM members WHERE name=?", (name,))[0]["id"]
        # SQL 预置 31 个主题（隔离库内造数，动作走 API）
        c = sqlite3.connect(DB)
        for i in range(31):
            c.execute("INSERT INTO member_memory(member,scope,key,text,version_id,updated_at) "
                      "VALUES(?, 'private', ?, 'v', 0, 0)", (name, f"pre{i:02d}"))
        c.commit(); c.close()
        res = await asyncio.gather(*[
            call(tok, "memory_write", scope="private", key=f"new{i}", text="v")
            for i in range(6)])
        n_ok = sum(1 for ok, _ in res if ok)
        total = sq("SELECT COUNT(*) AS c FROM member_memory WHERE member=? "
                   "AND scope='private' AND text != ''", (name,))[0]["c"]
        log(f"C1 round{rnd}: 并发写 6 个新主题，成功 {n_ok}，最终主题数 {total}（上限 32）")
        if total > 32:
            hits += 1
    log(f"C1 RESULT: {'竞态复现：主题数突破 32' if hits else 'live 未复现（窗口小，见语句级证据）'} rounds_hit={hits}")

# ---------- C1b: 语句级交错（memory_write 的 COUNT 检查与 INSERT 之间无锁） ----------
def c1b():
    name = "stmt-race"
    c = sqlite3.connect(DB)
    c.execute("INSERT OR IGNORE INTO members(name,role,token_hash,created_at) "
              "VALUES(?, 'member', 'x', 0)", (name,))
    c.execute("DELETE FROM member_memory WHERE member=?", (name,))
    for i in range(31):
        c.execute("INSERT INTO member_memory(member,scope,key,text,version_id,updated_at) "
                  "VALUES(?, 'private', ?, 'v', 0, 0)", (name, f"p{i:02d}"))
    c.commit(); c.close()
    barrier = threading.Barrier(2)
    def worker(key):
        conn = sqlite3.connect(DB, timeout=30)
        cnt = conn.execute("SELECT COUNT(*) AS c FROM member_memory "
                           "WHERE member=? AND scope='private' AND text != ''",
                           (name,)).fetchone()[0]
        assert cnt < 32, "check should pass for both"
        barrier.wait()  # 两边都过检查后再一起写 —— 正是无锁时的真实交错
        conn.execute("INSERT INTO member_memory_versions(member,scope,key,text,author,origin,source_refs,size,created_at) "
                     "VALUES(?, 'private', ?, 'v', 'stmt', 'self', '[]', 1, 0)", (name, key))
        vid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO member_memory(member,scope,key,text,version_id,updated_at) "
                     "VALUES(?, 'private', ?, 'v', ?, 0)", (name, key, vid))
        conn.commit(); conn.close()
    ts = [threading.Thread(target=worker, args=(f"s{i}",)) for i in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    total = sq("SELECT COUNT(*) AS c FROM member_memory WHERE member=? "
               "AND scope='private' AND text != ''", (name,))[0]["c"]
    log(f"C1b RESULT: 两路并发都过 COUNT 检查后落库，最终主题数 {total}（>32 即检查非原子）")

# ---------- C2: review_file 双 filing 竞态（live） ----------
async def c2():
    hits = 0
    for rnd in range(8):
        ok, t = await call(D, "task_create", room="pack-main",
                           title=f"[RV{rnd}] 审查竞态", body="b", assignee_name="alice")
        tid = t["task_id"]
        ok, claim = await call(A, "task_claim", task_id=tid)
        await call(A, "room_post", room="pack-main", kind="approval_request",
                   body=f"任务#{tid} 需要批准：竞态轮 {rnd}")
        r = await asyncio.gather(
            call(D, "review_file", task_id=tid, target_url="https://a.com",
                 owner_quote="q", member_desc="d", verdict="allow"),
            call(D, "review_file", task_id=tid, target_url="https://a.com",
                 owner_quote="q", member_desc="d", verdict="deny"))
        n_ok = sum(1 for ok, _ in r if ok)
        nlog = len(sq("SELECT id FROM review_log WHERE task_id=?", (tid,)))
        log(f"C2 round{rnd}: 双 filing 成功数 {n_ok}, review_log 行数 {nlog}")
        if n_ok > 1 or nlog > 1:
            hits += 1
    log(f"C2 RESULT: {'竞态复现：同一待批任务被双重决议' if hits else 'live 未复现'} rounds_hit={hits}")

# ---------- C3: message_delete 双删竞态（live） ----------
async def c3():
    hits = 0
    for rnd in range(8):
        ok, m = await call(A, "room_post", room="pack-main", body=f"双删测试 {rnd}")
        mid = m["message_id"]
        r = await asyncio.gather(*[call(A, "message_delete", message_id=mid)
                                   for _ in range(4)])
        n_ok = sum(1 for ok, _ in r if ok)
        naud = len(sq("SELECT id FROM audit WHERE action='message_delete' "
                      "AND detail=?", (f"message {mid}",)))
        log(f"C3 round{rnd}: 4 路并发删同一消息，成功 {n_ok}，审计行 {naud}")
        if n_ok > 1:
            hits += 1
    log(f"C3 RESULT: {'竞态复现：重复删除成功' if hits else 'live 未复现'} rounds_hit={hits}")

# ---------- C4: task_complete 双交活竞态（live） ----------
async def c4():
    hits = 0
    for rnd in range(8):
        ok, t = await call(D, "task_create", room="pack-main",
                           title=f"双交活 {rnd}", body="b", assignee_name="alice")
        tid = t["task_id"]
        ok, claim = await call(A, "task_claim", task_id=tid)
        tok = claim["claim_token"]
        await call(A, "run_start", task_id=tid, claim_token=tok)
        r = await asyncio.gather(*[
            call(A, "task_complete", task_id=tid, claim_token=tok, result=f"交活{rnd}")
            for _ in range(2)])
        n_ok = sum(1 for ok, _ in r if ok)
        nmsg = len(sq("SELECT id FROM messages WHERE task_id=? AND kind='report' "
                      "AND body LIKE '交活%'", (tid,)))
        log(f"C4 round{rnd}: 双交活成功 {n_ok}，交活消息 {nmsg}")
        if n_ok > 1:
            hits += 1
    log(f"C4 RESULT: {'竞态复现：重复交活' if hits else 'live 未复现'} rounds_hit={hits}")

# ---------- C5: sweeper 与 accept 竞态（进程内直调 sweep_once） ----------
def c5():
    sys.path.insert(0, BASE)
    os.chdir(BASE)
    import importlib.util
    spec = importlib.util.spec_from_file_location("hub14", os.path.join(BASE, "hub.py"))
    hub = importlib.util.module_from_spec(spec); spec.loader.exec_module(hub)
    hits = 0
    for rnd in range(12):
        # SQL 造一场：任务 claimed by alice → 转交 bob（pending）→ 把 expires_at 压到临界
        c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
        alice = c.execute("SELECT id FROM members WHERE name='alice'").fetchone()["id"]
        bob = c.execute("SELECT id FROM members WHERE name='bob'").fetchone()["id"]
        room = c.execute("SELECT id FROM rooms WHERE name='pack-main'").fetchone()["id"]
        cur = c.execute("INSERT INTO tasks(room_id,title,body,created_by,assignee,status,"
                        "claim_token,lease_expires,attempts,created_at,updated_at) "
                        "VALUES(?, 'sweep竞态', 'b', 1, ?, 'claimed', NULL, NULL, 1, 0, 0)",
                        (room, bob))
        tid = cur.lastrowid
        cur = c.execute("INSERT INTO task_transfers(task_id,from_member,to_member,reason,"
                        "actor,created_at,status,expires_at) VALUES(?, 'alice', 'bob', "
                        "NULL, 'alice', 0, 'pending', ?)", (tid, time.time() + 0.25))
        xid = cur.lastrowid
        c.commit(); c.close()
        barrier = threading.Barrier(2)
        accept_res = {}
        def do_accept():
            barrier.wait()
            async def go():
                return await call(B, "task_accept_transfer", task_id=tid)
            accept_res["r"] = asyncio.run(go())
        def do_sweep():
            barrier.wait()
            time.sleep(0.26)  # 等 expires_at 刚过
            hub.sweep_once()
        t1 = threading.Thread(target=do_accept); t2 = threading.Thread(target=do_sweep)
        t1.start(); t2.start(); t1.join(); t2.join()
        x = sq("SELECT status, accepted_at FROM task_transfers WHERE id=?", (xid,))[0]
        ok_accept = accept_res["r"][0]
        log(f"C5 round{rnd}: accept 成功={ok_accept} 转交行 status={x['status']} "
            f"accepted_at={x['accepted_at']}")
        if ok_accept and x["status"] == "expired":
            hits += 1
    log(f"C5 RESULT: {'竞态复现：已接受的转交被 sweeper 改标 expired' if hits else 'live 未复现（窗口≈ms级，见语句级证据）'} rounds_hit={hits}")

# ---------- C5b: 语句级——sweeper 过期 UPDATE 无 status 守卫 ----------
def c5b():
    src = open(os.path.join(BASE, "hub.py")).read().splitlines()
    for i, ln in enumerate(src, 1):
        if "SET status='expired'" in ln or "SET status='open', assignee=NULL" in ln and "sweep" in "".join(src[max(0,i-40):i]):
            log(f"C5b line {i}: {ln.strip()}")

# ---------- C6: 语句级——task_complete 的 UPDATE 无 claim_token 守卫（TOCTOU） ----------
def c6():
    # 造任务：alice 持有（token=TOK_A）
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    alice = c.execute("SELECT id FROM members WHERE name='alice'").fetchone()["id"]
    bob = c.execute("SELECT id FROM members WHERE name='bob'").fetchone()["id"]
    room = c.execute("SELECT id FROM rooms WHERE name='pack-main'").fetchone()["id"]
    cur = c.execute("INSERT INTO tasks(room_id,title,body,created_by,assignee,status,"
                    "claim_token,lease_expires,attempts,created_at,updated_at) "
                    "VALUES(?, 'TOCTOU', 'b', 1, ?, 'working', 'TOK_A', 9999999999, 1, 0, 0)",
                    (room, alice))
    tid = cur.lastrowid; c.commit()
    # conn A：_holder 校验（源码逐字条件）——此刻通过
    t = c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    holder_ok = (t["assignee"] == alice and t["claim_token"] == "TOK_A")
    # conn B：租约到期回收 + bob 重新认领（两条 UPDATE 均为源码语义）
    c.execute("UPDATE tasks SET status='open', assignee=NULL, claim_token=NULL, "
              "lease_expires=NULL WHERE id=?", (tid,))
    c.execute("UPDATE tasks SET status='claimed', assignee=?, claim_token='TOK_B', "
              "lease_expires=9999999999, attempts=attempts+1 WHERE id=? AND status='open'",
              (bob, tid))
    c.commit()
    # conn A：task_complete 的 UPDATE（源码逐字，无 token/assignee 守卫）
    cur = c.execute("UPDATE tasks SET status='review', result='迟到交活', envelope=NULL, "
                    "claim_token=NULL, lease_expires=NULL, updated_at=0 WHERE id=?", (tid,))
    c.commit(); c.close()
    row = sq("SELECT status, assignee, claim_token, result FROM tasks WHERE id=?", (tid,))[0]
    log(f"C6 RESULT: _holder 先检通过={holder_ok}；回收+重认领后旧持有人的无守卫 UPDATE "
        f"rowcount={cur.rowcount}；最终 status={row['status']} assignee={row['assignee']} "
        f"claim_token={row['claim_token']} result={row['result']}"
        "  => bob 的新认领被迟到交活覆盖（token 被清空、任务被抢进 review）")

# ---------- C7: 批准门伪造（非持有人触发规则自动决议） ----------
async def c7():
    await call(D, "rule_upsert", action="[SPF]", target="alice", effect="allow")
    ok, t = await call(D, "task_create", room="pack-main", title="[SPF] 伪造测试",
                       body="b", assignee_name="alice")
    tid = t["task_id"]
    ok, claim = await call(A, "task_claim", task_id=tid)
    # bob 不是持有人，冒发 approval_request
    ok, post = await call(B, "room_post", room="pack-main", kind="approval_request",
                          body=f"任务#{tid}「[SPF] 伪造测试」需要群主批准：bob 冒发")
    dec = sq("SELECT body FROM messages WHERE task_id=? AND kind='chat' AND body LIKE '批准%'",
             (tid,))
    log(f"C7 RESULT: 非持有人 bob 冒发 approval_request -> auto_decision="
        f"{post.get('auto_decision') if isinstance(post, dict) else post}，"
        f"房间内决议消息 {len(dec)} 条"
        + ("  => 批准门被非持有人触发" if dec else ""))

# ---------- C8: egress 审计含 URL 查询串 ----------
async def c8():
    marker = "SECRETQUERY123"
    await call(B, "egress_check", url=f"https://example.net/cb?token={marker}")
    rows = sq("SELECT detail FROM audit WHERE actor='egress' AND detail LIKE ?",
              (f"%{marker}%",))
    log(f"C8 RESULT: egress 审计 detail 含完整 URL 查询串（含 token 值）: {'是' if rows else '否'}")

async def amain():
    await c0(); await c1(); await c2(); await c3(); await c4(); await c7(); await c8()

asyncio.run(amain())
c1b(); c5(); c5b(); c6()
open(os.path.join(BASE, "concurrency-output.txt"), "w").write("\n".join(OUT) + "\n")
print("saved concurrency-output.txt")
