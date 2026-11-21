import asyncio, json, sqlite3, sys
sys.path.insert(0, ".")
from harness import call, try_call, client, pair_post, DISP, DB

res = []


def note(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  {extra}" if extra else ""), flush=True)


def dbq(sql, args=()):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return rows


def es(err):
    return err[:90] if isinstance(err, str) else ""


async def main():
    # ---------- 0. 工具面：工具数仍 74、room_post 新参数在 schema ----------
    async with client(DISP) as c:
        tools = await c.list_tools()
    note("tools == 74 (no new tools)", len(tools) == 74, f"got {len(tools)}")
    rp = [t for t in tools if t.name == "room_post"][0]
    props = set(rp.inputSchema.get("properties", {}).keys())
    note("room_post schema has payload + reply_to_id",
         {"payload", "reply_to_id"} <= props, f"props={sorted(props)}")

    # ---------- 1. 夹具 ----------
    ALICE = (await call(DISP, "member_register", name="alice"))["token"]
    BOB = (await call(DISP, "member_register", name="bob"))["token"]
    OWNER = (await call(DISP, "member_register", name="owner"))["token"]
    await call(DISP, "room_create", name="room-b")

    async def make_task(assignee, title):
        tid = (await call(DISP, "task_create", room="pack-main", title=title,
                          body="b", assignee_name=assignee))["task_id"]
        return tid

    T1 = await make_task("alice", "task-one")
    await call(ALICE, "task_claim", task_id=T1)
    T2 = await make_task("bob", "task-two")
    await call(BOB, "task_claim", task_id=T2)
    T3 = await make_task("", "task-three-open")
    T4 = await make_task("alice", "task-four")
    await call(ALICE, "task_claim", task_id=T4)

    OPTS = json.dumps({"options": ["方案甲", "方案乙"], "allow_input": True},
                      ensure_ascii=False)

    # ---------- 2. question 守卫分支 ----------
    ok, err = await try_call(ALICE, "room_post", room="pack-main", kind="question",
                             body="没有引用任务的问题", payload=OPTS)
    note("question without task ref rejected", not ok and "existing task" in err, es(err))
    ok, err = await try_call(ALICE, "room_post", room="pack-main", kind="question",
                             body="任务#99999 不存在的任务", payload=OPTS)
    note("question for nonexistent task rejected", not ok and "existing task" in err, es(err))
    ok, err = await try_call(BOB, "room_post", room="pack-main", kind="question",
                             body=f"任务#{T1} 别人的任务", payload=OPTS)
    note("question by non-executor rejected", not ok and "executing" in err, es(err))
    ok, err = await try_call(ALICE, "room_post", room="pack-main", kind="question",
                             body=f"任务#{T3} 没人认领的任务", payload=OPTS)
    note("question on unclaimed task rejected", not ok and "executing" in err, es(err))
    ok, err = await try_call(ALICE, "room_post", room="pack-main", kind="question",
                             body=f"任务#{T1} " + "长" * 2001, payload=OPTS)
    note("question body >2000 rejected", not ok and "too long" in err, es(err))

    # payload 分支（在 T1 上逐个试，均应被拒且不落库）
    bad_payloads = [
        ("empty payload", ""),
        ("non-json payload", "not json"),
        ("json array payload", "[1,2]"),
        ("one option", json.dumps({"options": ["只有一项"]})),
        ("seven options", json.dumps({"options": [f"项{i}" for i in range(7)]})),
        ("option >60 chars", json.dumps({"options": ["甲" * 61, "乙"]})),
        ("empty-string option", json.dumps({"options": ["", "乙"]})),
        ("non-string option", json.dumps({"options": [1, "乙"]})),
        ("neither options nor input", json.dumps({"options": [], "allow_input": False})),
        ("no options, allow_input false", json.dumps({"allow_input": False})),
        ("allow_input non-bool", json.dumps({"options": ["甲", "乙"], "allow_input": "yes"})),
    ]
    for label, pl in bad_payloads:
        ok, err = await try_call(ALICE, "room_post", room="pack-main", kind="question",
                                 body=f"任务#{T1} 载荷探针", payload=pl)
        note(f"question payload rejected: {label}", not ok, es(err))
    n = dbq("SELECT COUNT(*) AS n FROM messages WHERE kind='question'")[0]["n"]
    note("no question rows leaked from guard probes", n == 0, f"n={n}")

    # ---------- 3. question 正路 + 落库 + 回读 ----------
    q1 = await call(ALICE, "room_post", room="pack-main", kind="question",
                    body=f"任务#{T1} 用哪个方案？", payload=OPTS)
    Q1 = q1["message_id"]
    row = dbq("SELECT * FROM messages WHERE id=?", (Q1,))[0]
    stored = json.loads(row["payload"])
    note("question stored: kind/task_id/payload",
         row["kind"] == "question" and row["task_id"] == T1
         and stored == {"options": ["方案甲", "方案乙"], "allow_input": True},
         f"payload={row['payload']}")
    ok, err = await try_call(ALICE, "room_post", room="pack-main", kind="question",
                             body=f"任务#{T1} 再问一个", payload=OPTS)
    note("second unanswered question on same task rejected",
         not ok and "unanswered" in err, es(err))

    rd = await call(DISP, "room_read_new", room="pack-main", since_id=0, limit=200)
    by_id = {m["id"]: m for m in rd["messages"]}
    note("room_read_new carries payload for question",
         by_id[Q1]["payload"] == row["payload"], f"got {by_id[Q1].get('payload')}")
    chat_ids = [m for m in rd["messages"] if m["kind"] == "chat"]
    note("room_read_new payload is null for chat",
         all(m["payload"] is None for m in chat_ids), f"chats={len(chat_ids)}")

    ib = await call(ALICE, "inbox_pull", room="pack-main", since_id=0)
    ib_by_id = {m["id"]: m for m in ib["messages"]}
    note("inbox_pull carries payload for question",
         Q1 in ib_by_id and ib_by_id[Q1]["payload"] == row["payload"])

    # 纯输入型（bob 在自己的 T2 上）
    q2 = await call(BOB, "room_post", room="pack-main", kind="question",
                    body=f"任务#{T2} 请给个数字", payload=json.dumps({"allow_input": True}))
    Q2 = q2["message_id"]
    row2 = dbq("SELECT * FROM messages WHERE id=?", (Q2,))[0]
    note("pure-input question stored normalized",
         json.loads(row2["payload"]) == {"options": [], "allow_input": True},
         f"payload={row2['payload']}")

    # dispatcher 豁免执行者口径（T3 无人认领，dispatcher 可问）
    q3 = await call(DISP, "room_post", room="pack-main", kind="question",
                    body=f"任务#{T3} 调度员提问", payload=OPTS)
    Q3 = q3["message_id"]
    note("dispatcher may question on unclaimed task", Q3 > 0)

    # ---------- 4. question_answer 守卫分支 ----------
    chat = await call(DISP, "room_post", room="pack-main", kind="chat", body="普通消息")
    CHAT_ID = chat["message_id"]
    ok, err = await try_call(ALICE, "room_post", room="pack-main",
                             kind="question_answer", body="我来答",
                             reply_to_id=Q1)
    note("answer by ordinary member rejected", not ok and "dispatcher/owner" in err, es(err))
    ok, err = await try_call(DISP, "room_post", room="pack-main",
                             kind="question_answer", body="没指向")
    note("answer without reply_to_id rejected", not ok and "reply_to_id" in err, es(err))
    ok, err = await try_call(DISP, "room_post", room="pack-main",
                             kind="question_answer", body="指向 chat",
                             reply_to_id=CHAT_ID)
    note("answer pointing at chat rejected", not ok and "question message" in err, es(err))
    ok, err = await try_call(DISP, "room_post", room="pack-main",
                             kind="question_answer", body="指向不存在",
                             reply_to_id=999999)
    note("answer pointing at nonexistent rejected",
         not ok and "question message" in err, es(err))
    ok, err = await try_call(DISP, "room_post", room="room-b",
                             kind="question_answer", body="跨房答",
                             reply_to_id=Q3)
    note("answer in wrong room rejected", not ok and "in this room" in err, es(err))
    ok, err = await try_call(DISP, "room_post", room="pack-main",
                             kind="question_answer", body="答" * 2001,
                             reply_to_id=Q1)
    note("answer body >2000 rejected", not ok and "too long" in err, es(err))

    # 正路：dispatcher 答 Q1、owner 答 Q2 与 Q3
    a1 = await call(DISP, "room_post", room="pack-main", kind="question_answer",
                    body="选方案甲", reply_to_id=Q1)
    A1 = a1["message_id"]
    arow = dbq("SELECT * FROM messages WHERE id=?", (A1,))[0]
    note("answer stored: kind/reply_to_id/task_id",
         arow["kind"] == "question_answer" and arow["reply_to_id"] == Q1
         and arow["task_id"] == T1)
    ok, err = await try_call(OWNER, "room_post", room="pack-main",
                             kind="question_answer", body="重复答",
                             reply_to_id=Q1)
    note("duplicate answer rejected", not ok and "already answered" in err, es(err))
    a2 = await call(OWNER, "room_post", room="pack-main", kind="question_answer",
                    body="数字是 42", reply_to_id=Q2)
    note("owner may answer", a2["message_id"] > 0)
    a3 = await call(OWNER, "room_post", room="pack-main", kind="question_answer",
                    body="收到，按计划走", reply_to_id=Q3)
    note("owner may answer dispatcher's question", a3["message_id"] > 0)

    # 已答后可再问（T1）
    q4 = await call(ALICE, "room_post", room="pack-main", kind="question",
                    body=f"任务#{T1} 第二个问题", payload=OPTS)
    Q4 = q4["message_id"]
    note("new question allowed after previous answered", Q4 > 0)

    # question 不可编辑/删除（仅 chat 可）
    ok, err = await try_call(ALICE, "message_edit", message_id=Q4, new_body="改掉")
    note("question cannot be edited", not ok and "only chat" in err, es(err))
    ok, err = await try_call(ALICE, "message_delete", message_id=Q4)
    note("question cannot be deleted", not ok and "only chat" in err, es(err))

    # ---------- 5. 并发：双答只有一成、双问只有一成（_lock 串行） ----------
    r = await asyncio.gather(
        try_call(DISP, "room_post", room="pack-main", kind="question_answer",
                 body="并发答一", reply_to_id=Q4),
        try_call(OWNER, "room_post", room="pack-main", kind="question_answer",
                 body="并发答二", reply_to_id=Q4),
    )
    note("concurrent answers: exactly one wins",
         sum(1 for ok, _ in r if ok) == 1, f"r={[o for o, _ in r]}")
    r = await asyncio.gather(
        try_call(ALICE, "room_post", room="pack-main", kind="question",
                 body=f"任务#{T4} 并发问一", payload=OPTS),
        try_call(ALICE, "room_post", room="pack-main", kind="question",
                 body=f"任务#{T4} 并发问二", payload=OPTS),
    )
    note("concurrent questions: exactly one wins",
         sum(1 for ok, _ in r if ok) == 1, f"r={[o for o, _ in r]}")

    # ---------- 6. external：两 kind 被拒 + 审计 + 回读不受影响 ----------
    pc = await call(DISP, "pair_code_create", member_name="ext-q",
                    provider="codex", room="pack-main")
    st, resp = pair_post({"code": pc["pair_code"]})
    assert st == 200, (st, resp)
    EXT = resp["token"]
    before = dbq("SELECT COUNT(*) AS n FROM audit WHERE action='external_kind_denied'"
                 " AND actor='ext-q'")[0]["n"]
    ok, err = await try_call(EXT, "room_post", room="pack-main", kind="question",
                             body=f"任务#{T1} 外部提问", payload=OPTS)
    note("external question rejected", not ok and "not allowed for external" in err, es(err))
    ok, err = await try_call(EXT, "room_post", room="pack-main", kind="question_answer",
                             body="外部答", reply_to_id=Q1)
    note("external question_answer rejected",
         not ok and "not allowed for external" in err, es(err))
    after = dbq("SELECT COUNT(*) AS n FROM audit WHERE action='external_kind_denied'"
                " AND actor='ext-q'")[0]["n"]
    note("external kind denials audited", after - before == 2, f"{before}->{after}")
    # 入群水位之后的新 question / question_answer 才会进 external 的
    # 回读面；payload 原样带出（施工单第 5 条）。水位之前的 Q1 不可见。
    q5 = await call(DISP, "room_post", room="pack-main", kind="question",
                    body=f"任务#{T3} 入群后的问题", payload=OPTS)
    Q5 = q5["message_id"]
    row5 = dbq("SELECT payload FROM messages WHERE id=?", (Q5,))[0]
    a5 = await call(OWNER, "room_post", room="pack-main", kind="question_answer",
                    body="入群后的答复", reply_to_id=Q5)
    A5 = a5["message_id"]
    ibx = await call(EXT, "inbox_pull")
    ibx_by_id = {m["id"]: m for m in ibx["messages"]}
    note("external inbox: question payload carried through",
         Q5 in ibx_by_id and ibx_by_id[Q5]["payload"] == row5["payload"])
    note("external inbox: answer present, payload null",
         A5 in ibx_by_id and ibx_by_id[A5]["payload"] is None)
    note("external inbox: pre-watermark question invisible", Q1 not in ibx_by_id)
    rdx = await call(EXT, "room_read_new", room="pack-main", since_id=0, limit=200)
    rdx_by_id = {m["id"]: m for m in rdx["messages"]}
    note("external room_read_new: payload present above watermark",
         Q5 in rdx_by_id and rdx_by_id[Q5]["payload"] == row5["payload"])
    note("external room_read_new: pre-watermark question invisible",
         Q1 not in rdx_by_id)

    # ---------- 7. 回归：approval / decision / chat 旧口径 ----------
    ap = await call(ALICE, "room_post", room="pack-main", kind="approval_request",
                    body=f"任务#{T1} 请批准出站")
    note("approval_request still works for executor", ap["message_id"] > 0)
    ok, err = await try_call(BOB, "room_post", room="pack-main", kind="decision",
                             body=f"批准 任务#{T1}")
    note("decision still dispatcher-only", not ok)
    dec = await call(DISP, "room_post", room="pack-main", kind="decision",
                     body=f"批准 任务#{T1}")
    note("dispatcher decision still works", dec["message_id"] > 0)
    ok, err = await try_call(ALICE, "room_post", room="pack-main", kind="report",
                             body="伪造 report")
    note("kind=report still rejected via room_post", not ok)
    ok, err = await try_call(ALICE, "room_post", room="pack-main", kind="task_card",
                             body="伪造卡")
    note("kind=task_card still rejected via room_post", not ok)
    cm = await call(ALICE, "room_post", room="pack-main", kind="chat",
                    body="带 reply 的普通消息", reply_to_id=Q1)
    crow = dbq("SELECT reply_to_id FROM messages WHERE id=?", (cm["message_id"],))[0]
    note("chat via room_post still ignores reply_to_id (old behavior)",
         crow["reply_to_id"] is None)
    pm = await call(ALICE, "room_post", room="pack-main", kind="chat",
                    body="带 payload 的普通消息", payload=OPTS)
    prow = dbq("SELECT payload FROM messages WHERE id=?", (pm["message_id"],))[0]
    note("chat via room_post does not store payload", prow["payload"] is None)

    print(f"== {sum(res)}/{len(res)} PASS")
    return 0 if all(res) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
