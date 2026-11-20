"""staging52 / EXTERNAL-AGENT Phase B 件三：收件箱三工具全链。

在 run_tests.sh 已起的隔离中枢（18798、全新 DB）上跑，夹具自带前缀
s52-，与 Phase A 套件互不干扰。覆盖：pull/mark_read 游标、防盲回
两态、op_key 幂等、reply_to 链、水位截断在 inbox_pull 生效、白名单
放行 inbox_*、版本门、check-in 频度审计、限流与 room_post 共用窗口。
"""
import asyncio, base64, json, sys
sys.path.insert(0, ".")
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from harness import call, try_call, pair_post, DISP, DB, URL
import sqlite3

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


def dbx(sql, args=()):
    conn = sqlite3.connect(DB)
    conn.execute(sql, args)
    conn.commit()
    conn.close()


def audit_n(action, actor):
    return dbq("SELECT COUNT(*) AS n FROM audit WHERE action=? AND actor=?",
               (action, actor))[0]["n"]


def client_ua(tok, ua):
    return Client(StreamableHttpTransport(
        URL, headers={"Authorization": f"Bearer {tok}", "User-Agent": ua}))


async def call_ua(tok, ua, tool, **kw):
    async with client_ua(tok, ua) as c:
        r = await c.call_tool(tool, kw)
        return json.loads(r.content[0].text)


async def try_call_ua(tok, ua, tool, **kw):
    try:
        return True, await call_ua(tok, ua, tool, **kw)
    except Exception as e:
        return False, str(e)


async def make_external(name, room):
    r = await call(DISP, "pair_code_create", member_name=name, provider="codex",
                   room=room, display_name="", reclaim=False)
    st, resp = pair_post({"code": r["pair_code"]})
    assert st == 200, (name, st, resp)
    return resp["token"], resp["member_id"]


def png_b64():
    return base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode()


async def main():
    # ---------- 夹具 ----------
    reg = await call(DISP, "member_register", name="s52-alice")
    ALICE = reg["token"]
    await call(DISP, "room_create", name="s52-room")
    await call(DISP, "room_create", name="s52-other")
    # 入群前消息（external 兑换前落库，水位之外）
    p1 = await call(DISP, "room_post", room="s52-room", body="入群前消息一")
    p2 = await call(DISP, "room_post", room="s52-room", body="入群前消息二 @s52-ext")
    PRE_IDS = {p1["message_id"], p2["message_id"]}
    EXT, EXT_ID = await make_external("s52-ext", "s52-room")
    joined = dbq("SELECT joined_msg_id FROM members WHERE id=?", (EXT_ID,))[0]["joined_msg_id"]
    note("fixture: joined watermark == last pre-join msg", joined == max(PRE_IDS),
         f"joined={joined}")
    # 入群后消息：普通一条、@ 一条、任务卡一张
    m1 = await call(DISP, "room_post", room="s52-room", body="入群后普通消息")
    m2 = await call(DISP, "room_post", room="s52-room", body="@s52-ext 有活了")
    tk = await call(DISP, "task_create", room="s52-room", title="s52 文本任务",
                    body="请处理这条", assignee_name="s52-ext")
    TASK_ID = tk["task_id"]

    # ---------- 1. inbox_pull 默认（游标后未读 + 水位 + 标签） ----------
    dbx("UPDATE members SET last_heartbeat=0 WHERE id=?", (EXT_ID,))
    card_msg = dbq("SELECT id FROM messages WHERE task_id=? AND kind='task_card'",
                   (TASK_ID,))[0]["id"]
    pull = await call(EXT, "inbox_pull")
    ids = [x["id"] for x in pull["messages"]]
    note("pull: only post-join messages",
         set(ids) == {m1["message_id"], m2["message_id"], card_msg}
         and not (set(ids) & PRE_IDS), f"ids={ids}")
    by_id = {x["id"]: x for x in pull["messages"]}
    note("pull: @ flag on mention msg", by_id[m2["message_id"]]["mentions_me"] is True)
    note("pull: no @ flag on plain msg", by_id[m1["message_id"]]["mentions_me"] is False)
    note("pull: author server labels", by_id[m1["message_id"]]["author"] == "dispatcher"
         and by_id[m1["message_id"]]["author_role"] == "dispatcher"
         and by_id[m1["message_id"]]["author_kind"] == "internal")
    card = [x for x in pull["messages"] if x["kind"] == "task_card"]
    note("pull: task card carries status summary",
         len(card) == 1 and card[0]["task_id"] == TASK_ID
         and card[0]["task_status"] == "open" and card[0]["task_title"] == "s52 文本任务",
         f"{card}")
    hb = dbq("SELECT last_heartbeat FROM members WHERE id=?", (EXT_ID,))[0]["last_heartbeat"]
    note("pull: refreshes last_heartbeat (check-in)", hb and hb > 0, f"hb={hb}")
    note("pull: cursor reported == joined", pull["cursor"] == joined,
         f"cursor={pull['cursor']}")

    # ---------- 2. pull 不推进游标 / mark_read 推进且只进不退 ----------
    pull2 = await call(EXT, "inbox_pull")
    note("pull: repeat pull returns same unread (no auto-ack)",
         [x["id"] for x in pull2["messages"]] == ids)
    mr = await call(EXT, "inbox_mark_read")
    room_max = dbq("SELECT MAX(id) AS m FROM messages WHERE room_id="
                   "(SELECT id FROM rooms WHERE name='s52-room')")[0]["m"]
    note("mark_read: cursor advances to room max", mr["cursor"] == room_max,
         f"{mr['cursor']} vs {room_max}")
    pull3 = await call(EXT, "inbox_pull")
    note("mark_read: pull after ack is empty", pull3["messages"] == [])
    mr2 = await call(EXT, "inbox_mark_read", up_to_id=1)
    note("mark_read: never retreats", mr2["cursor"] == room_max)

    # ---------- 3. since_id 回看（仍受水位） ----------
    back = await call(EXT, "inbox_pull", since_id=joined)
    note("pull since_id: can re-read acked post-join",
         {x["id"] for x in back["messages"]} == set(ids))
    back0 = await call(EXT, "inbox_pull", since_id=0)
    note("pull since_id=0: watermark still floors (no pre-join leak)",
         not ({x["id"] for x in back0["messages"]} & PRE_IDS)
         and {x["id"] for x in back0["messages"]} == set(ids))

    # ---------- 4. 房间解析 ----------
    ok, err = await try_call(EXT, "inbox_pull", room="s52-other")
    note("pull: external naming another room rejected", not ok, str(err)[:70])
    ok, err = await try_call(ALICE, "inbox_pull")
    note("pull: internal member without room rejected", not ok)
    apull = await call(ALICE, "inbox_pull", room="s52-room")
    note("pull: internal member with explicit room works, own cursor",
         {x["id"] for x in apull["messages"]} >= PRE_IDS)

    # ---------- 5. inbox_send 基本 + reply_to 链 ----------
    s1 = await call(EXT, "inbox_send", body="外部成员第一条回复",
                    reply_to_id=m2["message_id"])
    note("send: ok after ack (no new msgs)", s1.get("sent") is True, f"{s1}")
    row = dbq("SELECT reply_to_id, kind FROM messages WHERE id=?",
              (s1["message_id"],))[0]
    note("send: reply_to_id persisted, kind=chat",
         row["reply_to_id"] == m2["message_id"] and row["kind"] == "chat")
    got = await call(DISP, "room_read_new", room="s52-room", since_id=0)
    mine = [x for x in got["messages"] if x["id"] == s1["message_id"]]
    note("send: visible in room for others", len(mine) == 1
         and mine[0]["author"] == "s52-ext")

    # ---------- 6. 防盲回两态 ----------
    m3 = await call(DISP, "room_post", room="s52-room", body="你睡着时来的新消息")
    blocked = await call(EXT, "inbox_send", body="盲回尝试")
    note("blind: send blocked when new msgs after cursor",
         blocked.get("sent") is False and blocked.get("blocked") == "new_messages",
         f"{blocked}")
    note("blind: new messages attached back",
         [x["id"] for x in blocked.get("new_messages", [])] == [m3["message_id"]])
    n = dbq("SELECT COUNT(*) AS n FROM messages WHERE body='盲回尝试'")[0]["n"]
    note("blind: blocked send not persisted", n == 0)
    forced = await call(EXT, "inbox_send", body="强发一条", ignore_new=True)
    note("blind: ignore_new=True forces send", forced.get("sent") is True)
    await call(EXT, "inbox_mark_read")

    # ---------- 7. op_key 幂等 ----------
    k1 = await call(EXT, "inbox_send", body="幂等原文", op_key="op-1")
    m4 = await call(DISP, "room_post", room="s52-room", body="幂等重试前的干扰消息")
    k2 = await call(EXT, "inbox_send", body="幂等改文重试", op_key="op-1")
    note("op_key: retry returns original message_id",
         k2.get("deduplicated") is True and k2["message_id"] == k1["message_id"],
         f"{k1} vs {k2}")
    n = dbq("SELECT COUNT(*) AS n FROM messages"
            " WHERE body IN ('幂等原文','幂等改文重试')")[0]["n"]
    note("op_key: only one row persisted", n == 1, f"n={n}")
    await call(EXT, "inbox_mark_read")

    # ---------- 8. reply_to 边界 ----------
    ok, err = await try_call(EXT, "inbox_send", body="回入群前",
                             reply_to_id=min(PRE_IDS), ignore_new=True)
    note("reply_to: pre-join target rejected (watermark)", not ok, str(err)[:70])
    other = await call(DISP, "room_post", room="s52-other", body="他房消息")
    ok, err = await try_call(EXT, "inbox_send", body="回他房",
                             reply_to_id=other["message_id"], ignore_new=True)
    note("reply_to: other-room target rejected", not ok, str(err)[:70])

    # ---------- 9. 附件绑入 inbox_send ----------
    att = await call(EXT, "attachment_put", room="s52-room", filename="a.png",
                     mime="image/png", data_base64=png_b64())
    s_att = await call(EXT, "inbox_send", body="带图回复",
                       attachment_ids=json.dumps([att["attachment_id"]]))
    note("send: attachment bound", s_att.get("sent") is True)
    arow = dbq("SELECT message_id FROM attachments WHERE id=?",
               (att["attachment_id"],))[0]
    note("send: attachment row points at the message",
         arow["message_id"] == s_att["message_id"])
    a_att = await call(ALICE, "attachment_put", room="s52-room", filename="b.png",
                       mime="image/png", data_base64=png_b64())
    ok, err = await try_call(EXT, "inbox_send", body="绑别人的图",
                             attachment_ids=json.dumps([a_att["attachment_id"]]),
                             ignore_new=True)
    note("send: cannot bind someone else's attachment", not ok, str(err)[:70])
    await call(EXT, "inbox_mark_read")

    # ---------- 10. 白名单放行 inbox_* / 名单外仍拒 ----------
    ok, _ = await try_call(EXT, "inbox_pull")
    note("whitelist: inbox_pull allowed for external", ok)
    ok, err = await try_call(EXT, "memory_read", scope="personal")
    note("whitelist: memory_read still denied for external", not ok
         and audit_n("external_tool_denied", "s52-ext") >= 1, str(err)[:70])

    # ---------- 11. 版本门 ----------
    ok, err = await try_call_ua(EXT, "pack-agent/0.9.0", "inbox_pull")
    note("version gate: pack-agent/0.9.0 rejected as outdated",
         not ok and "agent_outdated" in str(err), str(err)[:90])
    ok, r = await try_call_ua(EXT, "pack-agent/1.0.0", "inbox_pull")
    note("version gate: pack-agent/1.0.0 passes", ok)
    ok, r = await try_call_ua(EXT, "pack-agent/2.3.4", "inbox_pull")
    note("version gate: pack-agent/2.3.4 passes", ok)
    ok, err = await try_call_ua(EXT, "pack-agent/0.1.0", "inbox_send", body="旧脚本发送")
    note("version gate: send also gated", not ok and "agent_outdated" in str(err))

    # ---------- 12. check-in 频度审计 ----------
    note("audit: frequent check-in audited, not blocked",
         audit_n("inbox_checkin_frequent", "s52-ext") >= 1,
         f"n={audit_n('inbox_checkin_frequent', 's52-ext')}")
    note("audit: inbox_pull audited", audit_n("inbox_pull", "s52-ext") >= 3)

    # ---------- 13. 限流与 room_post 共用窗口（新成员单测） ----------
    FAST, FAST_ID = await make_external("s52-fast", "s52-room")
    await call(FAST, "inbox_mark_read")
    for i in range(3):
        await call(FAST, "room_post", room="s52-room", body=f"快发 room {i}")
    for i in range(2):
        r = await call(FAST, "inbox_send", body=f"快发 inbox {i}")
        assert r.get("sent") is True, r
    ok, err = await try_call(FAST, "inbox_send", body="第六条 inbox")
    note("rate: 6th message via inbox_send hard-rejected (shared window)",
         not ok and "rate_limited" in str(err), str(err)[:90])
    ok, err = await try_call(FAST, "room_post", room="s52-room", body="第六条 room")
    note("rate: 6th message via room_post also rejected (same window)",
         not ok and "rate_limited" in str(err), str(err)[:90])
    note("rate: rejection audited",
         audit_n("external_rate_limited", "s52-fast") >= 2)

    print(f"== {sum(res)}/{len(res)} PASS")
    return 0 if all(res) else 1


sys.exit(asyncio.run(main()))
