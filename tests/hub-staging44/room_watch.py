#!/usr/bin/env python3
"""Pack 活群 watcher：群主（author=owner）的聊天消息里 @ 到某成员时，
自动转成一张指派给该成员的任务卡，并由调度员在房间里回一行确认。

规则（与规格红线一致：只有人说的话算指令）：
- 只处理 kind=chat 且 author=owner 的消息；成员与调度员的消息一律不转。
- 模板直发（2026-11-16）：正文以「模板：」/「模板:」开头时按模板开卡
  （template_fire），先于 @ 匹配判定；模板名同宽容口径（精确优先、
  唯一前缀可、歧义回请写全名、查无此名回现有模板清单）；开卡失败
  （如模板含 {arg} 却没给参数）把原因回在房间里，不静默丢。
- @ 提及 = @ 或 ＠ 紧跟成员名（dispatcher/owner 自身除外）；宽容匹配：
  全名精确命中优先；否则大小写不敏感的唯一前缀也算命中（如 @a → absent）；
  前缀对上多个成员时不猜，调度员在房间里回一行请群主写全名。
- 指派给第一个被 @ 的成员；任务正文 = 去掉该 @ 提及后的原文
  （[ENG]/[M4A] 等路由标记原样保留，由对应执行体认领）。
- 每条消息只处理一次：进度持久化在 .room_watch_state.json，
  首启时从当前最新消息开始（不追溯历史）。

多房间（2026-11-15）：遍历 rooms 表全部房间（每轮经 room_list 刷新，
新房间自动纳入、从其当前最新消息开始）；转卡建在消息所在房间、
调度员回帖也回该房间。状态文件按房间存进度
{"rooms": {房间名: last_id}}；旧格式 {"last_id": N} 读到时把 N 当作
pack-main 的进度迁移，不重处理 pack-main 历史。
"""
import anyio
import base64
import binascii
import json
import os
import re
import time

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN = open(os.path.join(HERE, ".dispatcher_token")).read().strip()
URL = os.environ.get("PACK_WATCH_URL", "http://127.0.0.1:8794/mcp")
DEFAULT_ROOM = "pack-main"  # 旧格式状态迁移时单值 last_id 的归属房间
STATE = os.path.join(HERE, ".room_watch_state.json")
LOG = os.path.join(HERE, "room-watch.log")
POLL_SEC = 10


def log(msg):
    line = time.strftime("%F %T ") + msg
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, flush=True)


def parse(r):
    if getattr(r, "content", None):
        return json.loads(r.content[0].text)
    return r


_REPLY_SUFFIX = re.compile(r"\n\n\[pack-reply-v1:([A-Za-z0-9_-]+)\]\Z")


def strip_reply_metadata(body):
    """Only remove a valid App reply snapshot; preserve ordinary or malformed text."""
    if not isinstance(body, str):
        return body
    match = _REPLY_SUFFIX.search(body)
    if not match:
        return body
    token = match.group(1)
    try:
        raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        reply = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return body
    if not isinstance(reply, dict) or not isinstance(reply.get("author"), str) or not reply["author"] or not isinstance(reply.get("text"), str):
        return body
    return body[:match.start()]


def resolve_mention(body, targets):
    """宽容匹配 @ 提及，返回 (kind, name, span, candidates)。
    kind=hit：name 为命中的成员、span 为 @ 提及在 body 中的起止；
    kind=ambiguous：candidates 为前缀对上的多个成员；
    kind=none：没有指向成员的 @。
    单遍从左向右扫 @token：每个 token 先大小写不敏感精确匹配、
    再唯一前缀匹配；第一个能解出的 token 即命中，歧义 token 即报
    歧义——保证「指派给第一个被 @ 的成员」按正文顺序成立。
    staging30 修两处（第 2 轮深查 W1/W2，2026-11-17 合并入现行版）：
    - W1：旧实现先全量精确匹配（按 targets 列表顺序）再扫前缀，
      "@scout @absent" 会错派给 absent；现改为正文顺序单遍扫描；
    - W2：@ 前须为行首或非 [A-Za-z0-9_.-/] 字符，邮箱/URL 里的
      "foo@absent.com"、"x.com/@absent" 不再误触发（旧实现会命中）。"""
    lowered = {n.lower(): n for n in targets}
    for tm in re.finditer(r"(?<![A-Za-z0-9_.\-/])[@\uFF20]([A-Za-z0-9_-]+)", body):
        token = tm.group(1)
        low = token.lower()
        if low in lowered:
            return ("hit", lowered[low], (tm.start(), tm.end()), None)
        cands = [n for n in targets if n.lower().startswith(low)]
        if len(cands) == 1:
            return ("hit", cands[0], (tm.start(), tm.end()), None)
        if len(cands) > 1:
            return ("ambiguous", None, (tm.start(), tm.end()), cands)
    return ("none", None, None, None)


def parse_template_command(body, names):
    """识别模板直发指令「模板：<名> <参数>」（冒号全/半角皆可，名后参数可空）。
    不是模板指令返回 None；命中返回 ("hit", name, arg)；前缀对上多个返回
    ("ambiguous", token, candidates)；查无此名返回 ("unknown", token, None)。
    名字匹配与 @ 提及同一宽容口径：精确优先，否则唯一前缀。"""
    mm = re.match(r"^\s*模板[:：]\s*(\S+)(?:\s+(.*))?$", body.strip(), re.DOTALL)
    if not mm:
        return None
    token, arg = mm.group(1), (mm.group(2) or "").strip()
    if token in names:
        return ("hit", token, arg)
    low = token.lower()
    cands = [n for n in names if n.lower().startswith(low)]
    if len(cands) == 1:
        return ("hit", cands[0], arg)
    if len(cands) > 1:
        return ("ambiguous", token, cands)
    return ("unknown", token, None)


def load_state():
    """读状态并归一化为 {"rooms": {房间名: last_id}}。
    旧格式 {"last_id": N}（多房间改造前）把 N 迁移为 pack-main 的进度；
    读不到/格式不对则从空状态开始（各房间首启从最新消息起，不追溯）。"""
    try:
        st = json.load(open(STATE))
    except Exception:
        return {"rooms": {}}
    if not isinstance(st, dict):
        return {"rooms": {}}
    if isinstance(st.get("rooms"), dict):
        return {"rooms": dict(st["rooms"])}
    if st.get("last_id") is not None:
        return {"rooms": {DEFAULT_ROOM: st["last_id"]}}
    return {"rooms": {}}


def save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE)



async def _post_best_effort(c, room, body, tag):
    """确认/提示回帖：尽力而为。回帖失败只记日志，不让外层把已提交的
    开卡动作整条重试（会重复开卡）。"""
    try:
        await c.call_tool("room_post", {"room": room, "body": body})
    except Exception as e:
        log(f"confirm post failed ({tag}): {e!r}")


async def _handle_message(c, room, m, targets, tpl_names):
    """处理一条 owner chat 消息（模板直发 / @ 转卡 / 歧义与未知回复）。
    提交动作（template_fire / task_create）成功后回帖一律走
    _post_best_effort；提交动作本身抛错则原样上抛，由 run() 的
    失败计数决定重试或放弃（W4）。"""
    body = strip_reply_metadata(m["body"])
    tc = parse_template_command(body, tpl_names)
    if tc is not None:
        if tc[0] == "hit":
            _, tname, arg = tc
            try:
                r = parse(await c.call_tool("template_fire", {
                    "name": tname, "arg": arg, "room": room}))
            except Exception as e:
                await _post_best_effort(c, room,
                    f"模板「{tname}」没开成卡：{e}。补上参数再发一次。", "tpl-fail")
                log(f"template fire failed msg {m['id']} ({room}) tpl={tname}: {e!r}")
                return
            await _post_best_effort(c, room,
                f"已按模板「{tname}」开卡 #{r['task_id']} → @{r['assignee']}", "tpl-ok")
            log(f"template-fired msg {m['id']} ({room}) -> task {r['task_id']} tpl={tname}")
        elif tc[0] == "ambiguous":
            await _post_best_effort(c, room,
                f"「模板：{tc[1]}」对上了 {len(tc[2])} 个模板（{'、'.join(tc[2])}），写全名我再开卡。", "tpl-amb")
            log(f"ambiguous template in msg {m['id']} ({room}): {tc[1]} -> {tc[2]}")
        else:
            avail = "、".join(tpl_names) if tpl_names else "（暂无模板）"
            await _post_best_effort(c, room,
                f"没有叫「{tc[1]}」的模板。现有模板：{avail}。", "tpl-unknown")
            log(f"unknown template in msg {m['id']} ({room}): {tc[1]}")
        return
    kind, name, span, cands = resolve_mention(body, targets)
    if kind == "ambiguous":
        token = body[span[0] + 1:span[1]]
        await _post_best_effort(c, room,
            f"「@{token}」对上了 {len(cands)} 个成员（{'、'.join('@' + n for n in cands)}），写全名我再转任务。", "mention-amb")
        log(f"ambiguous mention in msg {m['id']} ({room}): @{token} -> {cands}")
        return
    if kind != "hit":
        return
    cleaned = (body[:span[0]] + body[span[1]:]).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    task_body = cleaned if cleaned else body
    title = task_body[:40]
    r = parse(await c.call_tool("task_create", {
        "room": room, "title": title, "body": task_body, "assignee_name": name}))
    tid = r["task_id"]
    await _post_best_effort(c, room, f"已转任务 #{tid} → @{name}", "mention-ok")
    log(f"converted msg {m['id']} ({room}) -> task {tid} for {name}")


MAX_MSG_FAILURES = 5  # 同一条消息连续处理失败上限，超过记日志放弃（防毒消息堵死整房）


MAX_POLL_ERRORS = 3  # 同一会话连续轮询失败达此数判定会话楔住、退出重连（P5）
RECONNECT_MAX_SEC = 30  # 重连退避上限（秒）


async def _watch_session(st, since, fail_counts):
    transport = StreamableHttpTransport(URL, headers={"Authorization": "Bearer " + TOKEN})
    async with Client(transport) as c:
        log(f"watch start rooms={list(since)}")
        poll_errors = 0
        while True:
            try:
                # 每轮刷新房间、模板与成员名单（W3：成员名单不再只在启动时
                # 读一次，新成员入群 / 改名后下一轮即生效）
                rooms = parse(await c.call_tool("room_list", {}))["rooms"]
                tpl_names = [t["name"] for t in
                             parse(await c.call_tool("template_list", {}))["templates"]]
                members = [m["name"] for m in parse(await c.call_tool("member_list", {}))["members"]]
                targets = [n for n in members if n not in ("owner", "dispatcher")]
                for rm in rooms:
                    room = rm["name"]
                    if room not in since:
                        # 新房间（含各房间首启）：从当前最新消息开始，不追溯历史
                        since[room] = rm["last_message_id"]
                        save_state(st)
                        log(f"init room {room} last_id={since[room]}")
                        continue
                    msgs = parse(await c.call_tool(
                        "room_read_new", {"room": room, "since_id": since[room], "limit": 50}))["messages"]
                    for m in msgs:
                        # W4：游标只在消息处理成功（或本就不需处理）后才推进；
                        # 处理抛错时不推进、下一轮重试，同条连续失败
                        # MAX_MSG_FAILURES 次后记日志放弃并越过，防整房堵死。
                        try:
                            if m["kind"] == "chat" and m["author"] == "owner":
                                await _handle_message(c, room, m, targets, tpl_names)
                        except Exception as e:
                            key = (room, m["id"])
                            fail_counts[key] = fail_counts.get(key, 0) + 1
                            if fail_counts[key] < MAX_MSG_FAILURES:
                                log(f"msg {m['id']} ({room}) failed "
                                    f"{fail_counts[key]}/{MAX_MSG_FAILURES}, will retry: {e!r}")
                                break
                            log(f"msg {m['id']} ({room}) giving up after "
                                f"{fail_counts[key]} failures: {e!r}")
                            fail_counts.pop(key, None)
                        since[room] = max(since[room], m["id"])
                        save_state(st)
                poll_errors = 0
            except Exception as e:
                # P4：连续同类错误只记首条与触发重连的一条，不再逐轮刷屏。
                poll_errors += 1
                if poll_errors == 1 or poll_errors >= MAX_POLL_ERRORS:
                    log(f"poll error {poll_errors}/{MAX_POLL_ERRORS}: {e!r}")
                if poll_errors >= MAX_POLL_ERRORS:
                    raise
            await anyio.sleep(POLL_SEC)


async def run():
    st = load_state()
    since = st["rooms"]
    fail_counts = {}
    backoff = 5
    while True:
        started = time.monotonic()
        try:
            await _watch_session(st, since, fail_counts)
        except Exception as e:
            # P5：启动期中枢未就绪 / 会话楔住都不再靠进程退出等 systemd 拉起，
            # 进程内退避重连；状态以落盘为准重读（游标只进不退）。
            if time.monotonic() - started > 120:
                backoff = 5
            log(f"watch session ended: {e!r}; reconnect in {backoff}s")
            await anyio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX_SEC)
            st = load_state()
            since = st["rooms"]


if __name__ == "__main__":
    anyio.run(run)
