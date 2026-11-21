"""Room read services; transport and authentication stay in hub.py."""
from __future__ import annotations

import json
from typing import Any


def list_rooms(context, member: dict[str, Any], include_archived: bool = False) -> dict[str, Any]:
    """List rooms and last-message cursors for an authenticated member."""
    connection = context.connect()
    try:
        rows = connection.execute(
            """SELECT r.id, r.name, r.created_at, r.archived_at,
                      COALESCE((SELECT MAX(m.id) FROM messages m
                                WHERE m.room_id=r.id), 0) AS last_message_id
               FROM rooms r
               WHERE (? OR r.archived_at IS NULL)
               ORDER BY r.id ASC""",
            (1 if include_archived else 0,),
        ).fetchall()
    finally:
        connection.close()

    result = []
    for row in rows:
        item = dict(row)
        item["archived"] = bool(item.pop("archived_at"))
        result.append(item)
    if (member.get("kind") or "") == "external":
        result = [item for item in result if item["id"] == member.get("bound_room_id")]
    return {"rooms": result}


def get_room_summary(
    context,
    member: dict[str, Any],
    room: str,
    summary_sections: tuple[str, ...],
) -> dict[str, Any]:
    """Read a room summary while enforcing external-member room binding."""
    connection = context.connect()
    try:
        room_row = connection.execute(
            "SELECT id FROM rooms WHERE name=?", (room,)
        ).fetchone()
        if not room_row:
            raise ValueError(f"no such room: {room}")
        if (member.get("kind") or "") == "external" and room_row["id"] != member.get("bound_room_id"):
            raise ValueError("forbidden: external members may only access their bound room")
        summary_row = connection.execute(
            "SELECT data, updated_at, updated_by FROM room_summaries WHERE room_id=?",
            (room_row["id"],),
        ).fetchone()
    finally:
        connection.close()

    if not summary_row:
        return {
            "room": room,
            "summary": {key: [] for key in summary_sections},
            "updated_at": None,
            "updated_by": None,
        }
    return {
        "room": room,
        "summary": json.loads(summary_row["data"]),
        "updated_at": summary_row["updated_at"],
        "updated_by": summary_row["updated_by"],
    }


DELETED_PLACEHOLDER = "（已删除）"


def read_new_messages(context, me, room: str, since_id: int = 0,
                      limit: int = 50, tail: bool = False) -> dict[str, Any]:
    """Read room messages with external-room and join-cursor restrictions."""
    limit = max(1, min(int(limit), 200))
    conn = context.connect()
    r = conn.execute("SELECT id FROM rooms WHERE name=?", (room,)).fetchone()
    if not r:
        conn.close()
        raise ValueError(f"no such room: {room}")
    if (me.get("kind") or "") == "external":
        if r["id"] != me["bound_room_id"]:
            conn.close()
            raise ValueError(
                "forbidden: external members may only read their bound room")
        # 入群水位硬截断（件二）：external 的读路径强制
        # effective_since = max(since_id, joined_msg_id)，传 0 也
        # 翻不到入群前的历史。
        since_id = max(int(since_id), me["joined_msg_id"] or 0)
    if tail:
        rows = conn.execute(
            """SELECT m.id, mb.name AS author, m.kind, m.body, m.task_id, m.created_at,
                      m.deleted_at, m.edited_at, m.payload, m.reply_to_id
               FROM messages m JOIN members mb ON mb.id=m.author_id
               WHERE m.room_id=? AND m.id>? ORDER BY m.id DESC LIMIT ?""",
            (r["id"], since_id, limit)).fetchall()
        rows = list(reversed(rows))
    else:
        rows = conn.execute(
            """SELECT m.id, mb.name AS author, m.kind, m.body, m.task_id, m.created_at,
                      m.deleted_at, m.edited_at, m.payload, m.reply_to_id
               FROM messages m JOIN members mb ON mb.id=m.author_id
               WHERE m.room_id=? AND m.id>? ORDER BY m.id ASC LIMIT ?""",
            (r["id"], since_id, limit)).fetchall()
    amap = {}
    if rows:
        mids = [x["id"] for x in rows]
        q = ("SELECT id, message_id, mime, size, original_name FROM attachments "
             "WHERE message_id IN (%s) ORDER BY id" % ",".join("?" * len(mids)))
        for a in conn.execute(q, mids).fetchall():
            amap.setdefault(a["message_id"], []).append(
                {"id": a["id"], "mime": a["mime"], "size": a["size"],
                 "filename": a["original_name"]})
    conn.close()
    out = []
    for x in rows:
        deleted = bool(x["deleted_at"])
        out.append({
            "id": x["id"], "author": x["author"], "kind": x["kind"],
            # 已删消息只回占位文本（删除时 body 已替换，这里再兜底一层，
            # 保证任何路径读到的都是占位、绝不漏原正文）
            "body": DELETED_PLACEHOLDER if deleted else x["body"],
            "task_id": x["task_id"], "created_at": x["created_at"],
            "deleted": deleted, "edited": bool(x["edited_at"]),
            "payload": None if deleted else x["payload"],
            # staging55: reply_to_id readback (staging53 missed it here;
            # inbox_pull had it). Deleted rows report None like payload.
            "reply_to_id": None if deleted else x["reply_to_id"],
            "attachments": amap.get(x["id"], []),
        })
    return {"messages": out}
