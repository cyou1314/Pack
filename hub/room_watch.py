#!/usr/bin/env python3
"""Pack 活群 watcher：群主（author=owner）的聊天消息里 @ 到某成员时，
自动转成一张指派给该成员的任务卡，并由调度员在房间里回一行确认。

规则（与规格红线一致：只有人说的话算指令）：
- 只处理 kind=chat 且 author=owner 的消息；成员与调度员的消息一律不转。
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


def resolve_mention(body, targets):
    """宽容匹配 @ 提及，返回 (kind, name, span, candidates)。
    kind=hit：name 为命中的成员、span 为 @ 提及在 body 中的起止；
    kind=ambiguous：candidates 为前缀对上的多个成员；
    kind=none：没有指向成员的 @。
    顺序：先全名精确匹配（与旧行为一致）；再逐个看 @token，
    大小写不敏感的精确/唯一前缀算命中，前缀对上多个则歧义。"""
    for name in targets:
        mm = re.search(r"[@\uFF20]" + re.escape(name) + r"(?![A-Za-z0-9_-])", body)
        if mm:
            return ("hit", name, (mm.start(), mm.end()), None)
    lowered = {n.lower(): n for n in targets}
    for tm in re.finditer(r"[@\uFF20]([A-Za-z0-9_-]+)", body):
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


async def run():
    st = load_state()
    since = st["rooms"]
    transport = StreamableHttpTransport(URL, headers={"Authorization": "Bearer " + TOKEN})
    async with Client(transport) as c:
        members = [m["name"] for m in parse(await c.call_tool("member_list", {}))["members"]]
        targets = [n for n in members if n not in ("owner", "dispatcher")]
        log(f"watch start targets={targets} rooms={list(since)}")
        while True:
            try:
                # 每轮经 room_list 刷新房间集合，新房间自动纳入
                rooms = parse(await c.call_tool("room_list", {}))["rooms"]
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
                        since[room] = max(since[room], m["id"])
                        save_state(st)
                        if m["kind"] != "chat" or m["author"] != "owner":
                            continue
                        body = m["body"]
                        kind, name, span, cands = resolve_mention(body, targets)
                        if kind == "ambiguous":
                            token = body[span[0] + 1:span[1]]
                            await c.call_tool("room_post", {
                                "room": room,
                                "body": f"「@{token}」对上了 {len(cands)} 个成员（{'、'.join('@' + n for n in cands)}），写全名我再转任务。"})
                            log(f"ambiguous mention in msg {m['id']} ({room}): @{token} -> {cands}")
                            continue
                        if kind != "hit":
                            continue
                        cleaned = (body[:span[0]] + body[span[1]:]).strip()
                        cleaned = re.sub(r"\s+", " ", cleaned)
                        task_body = cleaned if cleaned else body
                        title = task_body[:40]
                        r = parse(await c.call_tool("task_create", {
                            "room": room, "title": title, "body": task_body, "assignee_name": name}))
                        tid = r["task_id"]
                        await c.call_tool("room_post", {
                            "room": room, "body": f"已转任务 #{tid} → @{name}"})
                        log(f"converted msg {m['id']} ({room}) -> task {tid} for {name}")
            except Exception as e:
                log(f"poll error: {e!r}")
            await anyio.sleep(POLL_SEC)


if __name__ == "__main__":
    anyio.run(run)
