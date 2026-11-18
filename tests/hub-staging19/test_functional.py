"""staging14 复检·功能面：鉴权边界 / 限额口径 / 数据完整性 / 审计完整性。
成员身份在 tokens.json（由本脚本首跑生成）。"""
import asyncio, json, os, sqlite3, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import call, client, DISP

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "pack.db")
PASS, FAIL = [], []

def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))

def sq(q, args=()):
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    rows = c.execute(q, args).fetchall(); c.close()
    return rows

async def main():
    T = {"dispatcher": DISP}
    # 成员注册（harness 已注册过则从 DB 无法取明文 token，改用全新名字）
    async with client(DISP) as c:
        for name in ("alice", "bob", "owner"):
            try:
                r = await c.call_tool("member_register", {"name": name})
                T[name] = json.loads(r.content[0].text)["token"]
            except Exception:
                pass
    if "alice" not in T:
        # 已注册过：直接读 tokens 备份文件
        T.update(json.load(open(os.path.join(BASE, "tokens.json"))))
    else:
        json.dump(T, open(os.path.join(BASE, "tokens.json"), "w"))
        os.chmod(os.path.join(BASE, "tokens.json"), 0o600)
    A, B, O, D = T["alice"], T["bob"], T["owner"], T["dispatcher"]

    # ---------- 1. 鉴权边界 ----------
    ok, e = await call(A, "rule_upsert", action="[ENG]", target="", effect="allow")
    check("auth: member rule_upsert 被拒", not ok and "dispatcher" in str(e), str(e))
    ok, e = await call(A, "egress_rule_upsert", pattern="example.com", effect="allow")
    check("auth: member egress_rule_upsert 被拒", not ok)
    ok, e = await call(A, "task_release", task_id=1)
    check("auth: member task_release 被拒", not ok)
    ok, e = await call(A, "run_list")
    check("auth: member run_list 被拒", not ok)
    ok, e = await call(A, "task_transfers_list", task_id=1)
    check("auth: member task_transfers_list 被拒", not ok)
    ok, e = await call(A, "room_create", name="evil-room")
    check("auth: member room_create 被拒", not ok)
    ok, e = await call(A, "memory_rollback", scope="private-core", key="", version_id=1)
    check("auth: member memory_rollback 被拒(forbidden)", not ok and "forbidden" in str(e), str(e))
    ok, e = await call(A, "memory_versions", scope="private", key="x", include_text=True)
    check("auth: member memory_versions include_text 被拒", not ok and "forbidden" in str(e), str(e))
    ok, e = await call("pk-bad-token", "room_list")
    check("auth: 坏 token 被拒", not ok)

    # 跨成员读记忆 → forbidden + 审计留痕
    await call(A, "memory_write", scope="private-core", key="", text="alice core v1")
    n0 = len(sq("SELECT id FROM audit WHERE action='memory_access_denied'"))
    ok, e = await call(B, "memory_read", scope="private-core", member="alice")
    n1 = len(sq("SELECT id FROM audit WHERE action='memory_access_denied'"))
    check("auth: 跨成员读 private-core forbidden", not ok and "forbidden" in str(e), str(e))
    check("audit: 跨成员读留 memory_access_denied", n1 == n0 + 1)
    # member 参数指向不存在成员（调度员）→ not_found
    ok, e = await call(D, "memory_read", scope="private-core", member="ghost")
    check("auth: 调度员代查不存在成员 not_found", not ok and "not_found" in str(e), str(e))
    # 成员写 room-shared → forbidden
    ok, e = await call(A, "memory_write", scope="room-shared", key="", text="x")
    check("auth: 成员写 room-shared 被拒", not ok and "forbidden" in str(e), str(e))
    # 成员自报 origin=dispatcher → invalid_argument
    ok, e = await call(A, "memory_write", scope="private-core", key="", text="y", origin="dispatcher")
    check("auth: 成员自报 origin=dispatcher 被拒", not ok and "invalid_argument" in str(e), str(e))

    # ---------- 2. 限额口径 ----------
    # L0 字节硬顶：4096 字节整好过、4097 拒且不落版
    ok, _ = await call(A, "memory_write", scope="private-core", key="", text="a" * 4096)
    check("limit: L0 4096B 通过", ok)
    nv0 = len(sq("SELECT id FROM member_memory_versions WHERE member='alice' AND scope='private-core'"))
    ok, e = await call(A, "memory_write", scope="private-core", key="", text="a" * 4097)
    nv1 = len(sq("SELECT id FROM member_memory_versions WHERE member='alice' AND scope='private-core'"))
    check("limit: L0 4097B 整笔拒绝", not ok and "memory_limit_exceeded" in str(e), str(e))
    check("limit: L0 超限不落版", nv0 == nv1)
    # CJK：2048 个汉字 = 6144 字节 > 4096 → 拒（证字节计数而非字符）
    ok, e = await call(A, "memory_write", scope="private-core", key="", text="汉" * 2048)
    check("limit: L0 按字节计（2048 汉字=6144B 被拒）", not ok and "memory_limit_exceeded" in str(e), str(e))
    # L1 字符硬顶：20000 码点过、20001 拒不落版；CJK 20000 字（60000B）应过（证字符计数）
    ok, e = await call(A, "memory_write", scope="private", key="big", text="汉" * 20000)
    check("limit: L1 20000 CJK 字符通过（字符计数）", ok, str(e))
    nv0 = len(sq("SELECT id FROM member_memory_versions WHERE member='alice' AND scope='private' AND key='big'"))
    ok, e = await call(A, "memory_write", scope="private", key="big", text="x" * 20001)
    nv1 = len(sq("SELECT id FROM member_memory_versions WHERE member='alice' AND scope='private' AND key='big'"))
    check("limit: L1 20001 字符拒绝且不落版", not ok and nv0 == nv1, str(e))
    # 主题数 ≤32
    for i in range(31):
        await call(B, "memory_write", scope="private", key=f"t{i:02d}", text="v")
    ok, e = await call(B, "memory_write", scope="private", key="t31", text="v")
    check("limit: 第 32 个主题通过", ok, str(e))
    ok, e = await call(B, "memory_write", scope="private", key="t32", text="v")
    check("limit: 第 33 个主题被拒 memory_topic_limit", not ok and "memory_topic_limit" in str(e), str(e))
    # 删一个主题（墓碑）后可再建
    await call(B, "memory_write", scope="private", key="t00", text=None)
    ok, e = await call(B, "memory_write", scope="private", key="t32", text="v")
    check("limit: 删主题后名额释放", ok, str(e))
    # 软顶：alice 已有 big=20000；再写 10 篇 19000 字主题 → 总量 210000 > 200000，只警告不拒绝
    for i in range(10):
        ok, e = await call(A, "memory_write", scope="private", key=f"bulk{i}", text="y" * 19000)
        if not ok:
            break
    check("limit: L1 总量超软顶仍可写（只警告）", ok, str(e))
    rows = sq("SELECT detail FROM audit WHERE action='memory_soft_cap' AND actor='system'")
    check("audit: 软顶触发 memory_soft_cap 审计", len(rows) >= 1)
    # 审计不含记忆正文：写特征串后全局搜 audit.detail
    secret = "SECRET-MEM-BODY-9f3k"
    await call(A, "memory_write", scope="private-core", key="", text=secret)
    leak = sq("SELECT id FROM audit WHERE detail LIKE ?", (f"%{secret}%",))
    check("audit: 记忆正文不进审计", len(leak) == 0)
    leak2 = sq("SELECT id FROM messages WHERE body LIKE ?", (f"%{secret}%",))
    check("audit: 记忆正文不进房间消息", len(leak2) == 0)

    # ---------- 3. 出口管制 ----------
    ok, e = await call(B, "egress_check", url="not a url")
    check("egress: 畸形 URL 拒绝", not ok and "malformed" in str(e), str(e))
    ok, e = await call(B, "egress_check", url="ftp://example.com/x")
    check("egress: 非 http scheme 拒绝", not ok)
    ok, out = await call(B, "egress_check", url="https://unknown-site.example.org/path")
    check("egress: 无规则无点名 → ask", ok and out["decision"] == "ask", str(out))
    # 群主点名：dispatcher 建的任务正文含 host → allow；成员自建任务（经 dispatcher 建但 created_by 恒调度员，
    # 用 owner 名成员建任务走 task_create 不行——task_create 仅调度员。故点名反例：owner 建任务需其有调度权，
    # 这里验证 created_by=owner 的行需直查 DB 构造，见 test 第 2 段 egress-owner.py）
    ok, t = await call(D, "task_create", room="pack-main", title="抓取测试",
                       body="请抓 https://docs.python.org/3/ 的内容", assignee_name="alice")
    tid = t["task_id"]
    ok, out = await call(B, "egress_check", url="https://docs.python.org/3/library", task_id=tid)
    check("egress: 调度员任务点名同站 → allow", ok and out["decision"] == "allow", str(out))
    ok, out = await call(B, "egress_check", url="https://sub.docs.python.org/x", task_id=tid)
    check("egress: 点名覆盖子域 → allow", ok and out["decision"] == "allow", str(out))
    ok, out = await call(B, "egress_check", url="https://other.org/", task_id=tid)
    check("egress: 点名不覆盖异站 → ask", ok and out["decision"] == "ask", str(out))
    # ask 规则压过点名
    await call(D, "egress_rule_upsert", pattern="python.org", effect="ask")
    ok, out = await call(B, "egress_check", url="https://docs.python.org/3/", task_id=tid)
    check("egress: ask 规则压过点名", ok and out["decision"] == "ask", str(out))
    await call(D, "egress_rule_upsert", pattern="python.org", effect="allow")
    ok, out = await call(B, "egress_check", url="https://www.python.org/", task_id=0)
    check("egress: allow 规则命中（父域覆盖子域）", ok and out["decision"] == "allow", str(out))
    ok, e = await call(B, "egress_check", url="https://x.com/", task_id=99999)
    check("egress: 不存在 task_id 干净报错", not ok and "no such task" in str(e), str(e))
    rows = sq("SELECT id FROM audit WHERE actor='egress' AND action='egress_check'")
    check("audit: egress 每次判定留审计", len(rows) >= 6, f"n={len(rows)}")

    # ---------- 4. 规则库 + 审查 ----------
    ok, t = await call(D, "task_create", room="pack-main", title="[ENG] 规则测试",
                       body="b", assignee_name="alice")
    tid2 = t["task_id"]
    await call(D, "rule_upsert", action="[ENG]", target="alice", effect="allow")
    ok, claim = await call(A, "task_claim", task_id=tid2)
    tok2 = claim["claim_token"]
    ok, post = await call(A, "room_post", room="pack-main", kind="approval_request",
                          body=f"任务#{tid2}「[ENG] 规则测试」需要群主批准：测试")
    check("rule: 命中规则自动决议", ok and post.get("auto_decision") == "allow", str(post))
    rows = sq("SELECT id FROM audit WHERE actor='rule-engine' AND action='approval_auto_allow'")
    check("audit: 规则决议 actor=rule-engine", len(rows) >= 1)
    # 已被规则决议 → review_file 拒绝重复 filing
    ok, e = await call(D, "review_file", task_id=tid2, target_url="https://x.com",
                       owner_quote="q", member_desc="d", verdict="deny")
    check("review: 已决议任务拒绝重复 filing", not ok and "already decided" in str(e), str(e))
    # 无批准请求的任务 → review_file 拒绝
    ok, t3 = await call(D, "task_create", room="pack-main", title="无请求任务", body="b")
    ok, e = await call(D, "review_file", task_id=t3["task_id"], target_url="https://x.com",
                       owner_quote="q", member_desc="d", verdict="allow")
    check("review: 无批准请求拒绝 filing", not ok and "no approval request" in str(e), str(e))
    # 正常审查链：未命中规则的任务
    ok, t4 = await call(D, "task_create", room="pack-main", title="[XYZ] 审查测试",
                        body="b", assignee_name="bob")
    tid4 = t4["task_id"]
    ok, claim = await call(B, "task_claim", task_id=tid4)
    await call(B, "room_post", room="pack-main", kind="approval_request",
               body=f"任务#{tid4} 需要批准：抓取")
    ok, out = await call(D, "review_file", task_id=tid4, target_url="https://a.com",
                         owner_quote="群主原话", member_desc="成员描述", verdict="allow")
    check("review: 未决议任务 filing 成功", ok and out["verdict"] == "allow", str(out))
    rows = sq("SELECT id FROM review_log WHERE task_id=?", (tid4,))
    check("review: review_log 留痕", len(rows) == 1)
    ok, e = await call(D, "review_file", task_id=tid4, target_url="https://a.com",
                       owner_quote="q", member_desc="d", verdict="deny")
    check("review: 审查后拒绝二次 filing", not ok and "already decided" in str(e), str(e))
    # 证据缺失
    ok, e = await call(D, "review_file", task_id=tid4, target_url=" ",
                       owner_quote="q", member_desc="d", verdict="allow")
    check("review: 证据为空拒绝", not ok and "missing evidence" in str(e), str(e))

    # ---------- 5. 信封与退避 ----------
    ok, t5 = await call(D, "task_create", room="pack-main", title="信封测试", body="b",
                        assignee_name="alice")
    tid5 = t5["task_id"]
    ok, claim = await call(A, "task_claim", task_id=tid5)
    tok5 = claim["claim_token"]
    ok, e = await call(A, "task_complete", task_id=tid5, claim_token=tok5, result="r",
                       envelope={"verdict": "bogus", "summary": "s"})
    row = sq("SELECT status, envelope FROM tasks WHERE id=?", (tid5,))[0]
    check("envelope: 非法信封整笔打回、状态不变", not ok and "invalid_envelope" in str(e)
          and row["status"] == "claimed" and row["envelope"] is None, str(e))
    ok, out = await call(A, "task_complete", task_id=tid5, claim_token=tok5, result="ignored",
                         envelope={"verdict": "done", "summary": "做完了",
                                   "artifacts": [{"kind": "file", "path": "/tmp/x"}]})
    row = sq("SELECT status, result FROM tasks WHERE id=?", (tid5,))[0]
    check("envelope: 合法信封交活进 review、result=summary", ok and row["status"] == "review"
          and row["result"] == "做完了", str(out))
    # task_block fail_class 非法值 → unknown 不报错
    ok, t6 = await call(D, "task_create", room="pack-main", title="退避测试", body="b",
                        assignee_name="alice")
    tid6 = t6["task_id"]
    ok, claim = await call(A, "task_claim", task_id=tid6)
    ok, out = await call(A, "task_block", task_id=tid6, claim_token=claim["claim_token"],
                         reason="卡住", fail_class="nonsense")
    check("backoff: 非法 fail_class 记 unknown 不报错", ok and out["fail_class"] == "unknown", str(out))
    row = sq("SELECT status, next_claim_at FROM tasks WHERE id=?", (tid6,))[0]
    import time as _t
    delta = row["next_claim_at"] - _t.time()
    check("backoff: unknown 基数 300s×2^0 已预置", row["status"] == "blocked" and 250 < delta <= 300,
          f"delta={delta:.0f}")
    # task_release 对非 blocked 拒绝、对 blocked 放回
    ok, e = await call(D, "task_release", task_id=tid5)
    check("release: 非 blocked 任务拒绝放回", not ok and "not blocked" in str(e), str(e))
    ok, out = await call(D, "task_release", task_id=tid6)
    check("release: blocked 放回 open", ok and out["status"] == "open", str(out))
    # 退避未到期 claim 被拒且报错含 backoff
    ok, e = await call(A, "task_claim", task_id=tid6)
    check("backoff: 退避期内 claim 被拒", not ok and "backoff" in str(e), str(e))
    ok, out = await call(D, "task_clear_backoff", task_id=tid6)
    ok, claim = await call(A, "task_claim", task_id=tid6)
    check("backoff: 清退避后可认领", ok and "claim_token" in claim, str(claim))
    # clear_backoff 对非 open 拒绝
    ok, e = await call(D, "task_clear_backoff", task_id=tid5)
    check("clear_backoff: 非 open 拒绝", not ok and "not open" in str(e), str(e))

    # ---------- 6. 消息删改 ----------
    ok, m = await call(A, "room_post", room="pack-main", body="alice 的消息")
    mid = m["message_id"]
    ok, e = await call(B, "message_delete", message_id=mid)
    check("msg: 非作者删除被拒", not ok and "only the author" in str(e), str(e))
    ok, e = await call(B, "message_edit", message_id=mid, new_body="篡改")
    check("msg: 非作者编辑被拒", not ok and "only the author" in str(e), str(e))
    ok, out = await call(A, "message_edit", message_id=mid, new_body="alice 改过")
    row = sq("SELECT body, edited_at FROM messages WHERE id=?", (mid,))[0]
    check("msg: 作者编辑成功且记 edited_at", ok and row["body"] == "alice 改过" and row["edited_at"], str(out))
    ok, out = await call(A, "message_delete", message_id=mid)
    ok, msgs = await call(A, "room_read_new", room="pack-main", since_id=mid - 1, limit=5)
    mine = [x for x in msgs["messages"] if x["id"] == mid]
    check("msg: 删除后读取只见占位", ok and mine and mine[0]["deleted"] and "已删除" in mine[0]["body"], str(mine))
    row = sq("SELECT body FROM messages WHERE id=?", (mid,))[0]
    check("msg: 删除后 DB 正文已替换为占位（原正文不留存）", "alice" not in row["body"], row["body"])
    ok, e = await call(A, "message_delete", message_id=mid)
    check("msg: 重复删除被拒", not ok and "already deleted" in str(e), str(e))
    ok, e = await call(A, "message_edit", message_id=mid, new_body="x")
    check("msg: 已删消息不可编辑", not ok and "already deleted" in str(e), str(e))
    # 结构性消息不可删：task_card
    cards = sq("SELECT id FROM messages WHERE kind='task_card' ORDER BY id LIMIT 1")
    ok, e = await call(D, "message_delete", message_id=cards[0]["id"])
    check("msg: task_card 不可删", not ok and "only chat" in str(e), str(e))
    ok, e = await call(A, "message_delete", message_id=999999)
    check("msg: 不存在消息干净报错", not ok and "no such message" in str(e), str(e))

    # ---------- 7. 多房间 ----------
    ok, e = await call(D, "room_create", name="bad name")
    check("room: 含空白名被拒", not ok)
    ok, e = await call(D, "room_create", name="a/b")
    check("room: 含斜杠名被拒", not ok)
    ok, e = await call(D, "room_create", name="x" * 41)
    check("room: 超 40 名被拒", not ok)
    ok, out = await call(D, "room_create", name="review-room")
    check("room: 建房成功", ok and out["name"] == "review-room", str(out))
    ok, e = await call(D, "room_create", name="review-room")
    check("room: 重名被拒", not ok and "already exists" in str(e), str(e))
    ok, out = await call(B, "room_list")
    names = [r["name"] for r in out["rooms"]]
    check("room: room_list 含新房且成员可调", "review-room" in names, str(names))

    # ---------- 8. 转交完整性（顺序路径） ----------
    ok, t7 = await call(D, "task_create", room="pack-main", title="转交测试", body="b",
                        assignee_name="alice")
    tid7 = t7["task_id"]
    ok, claim = await call(A, "task_claim", task_id=tid7)
    tok7 = claim["claim_token"]
    ok, e = await call(A, "task_transfer", task_id=tid7, claim_token="wrong", to_member="bob")
    check("transfer: 错 token 转交被拒", not ok and "not the current holder" in str(e), str(e))
    ok, e = await call(A, "task_transfer", task_id=tid7, claim_token=tok7, to_member="ghost")
    check("transfer: 目标不存在被拒", not ok and "no such member" in str(e), str(e))
    # owner 在库里 role='member'：修复前转交可成功（复检实证），修复后须按名拒绝
    ok, t7b = await call(D, "task_create", room="pack-main", title="转交owner探针", body="b",
                         assignee_name="alice")
    ok, claimb = await call(A, "task_claim", task_id=t7b["task_id"])
    ok, e = await call(A, "task_transfer", task_id=t7b["task_id"],
                       claim_token=claimb["claim_token"], to_member="owner")
    check("transfer: 转给 owner 被拒（按名补检）", not ok and "owner" in str(e), str(e))
    ok, out = await call(A, "task_transfer", task_id=tid7, claim_token=tok7, to_member="bob",
                         reason="换手")
    check("transfer: 正常转交 pending", ok and out["transfer_status"] == "pending", str(out))
    # pending 期间无人持有 token，pending 检查只有调度员强制转交能走到
    ok, e = await call(D, "task_transfer", task_id=tid7, claim_token="", to_member="alice")
    check("transfer: 已有 pending 时再转交被拒", not ok and "pending" in str(e), str(e))
    ok, e = await call(A, "task_accept_transfer", task_id=tid7)
    check("transfer: 非接收方领令牌被拒", not ok and "not to" in str(e) or not ok, str(e))
    ok, out = await call(B, "task_accept_transfer", task_id=tid7)
    check("transfer: 接收方领令牌成功", ok and "claim_token" in out, str(out))
    ok, e = await call(B, "task_accept_transfer", task_id=tid7)
    check("transfer: 重复领取被拒", not ok and "already accepted" in str(e), str(e))
    rows = sq("SELECT status FROM task_transfers WHERE task_id=?", (tid7,))
    check("transfer: 转交行 accepted 落库", len(rows) == 1 and rows[0]["status"] == "accepted")

    print(f"\n==== functional: {len(PASS)} passed, {len(FAIL)} failed ====")
    if FAIL:
        print("FAILED:", FAIL)
    return 1 if FAIL else 0

sys.exit(asyncio.run(main()))
