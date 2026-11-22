"""Room read services; transport and authentication stay in hub.py."""
from __future__ import annotations

import json
import sqlite3
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



def _validate_room_name(name: str) -> None:
    if not name:
        raise ValueError("room name must not be empty")
    if len(name) > 40:
        raise ValueError(f"room name too long: {len(name)} > 40")
    if any(char.isspace() for char in name) or "/" in name or "\\" in name:
        raise ValueError(f"room name must not contain whitespace or slashes: {name!r}")


def create_room(context, member, name: str) -> dict[str, Any]:
    """Create a room and write its audit record in the same transaction."""
    _validate_room_name(name)
    connection = context.connect()
    try:
        cursor = connection.execute(
            "INSERT INTO rooms(name,created_at) VALUES(?,?)",
            (name, context.clock()),
        )
        room_id = cursor.lastrowid
        context.audit(connection, member["name"], "room_create", f"room {room_id}: {name}")
        connection.commit()
    except sqlite3.IntegrityError as error:
        raise ValueError(f"room already exists: {name}") from error
    finally:
        connection.close()
    return {"room_id": room_id, "name": name}


def rename_room(context, member, room: str, new_name: str) -> dict[str, Any]:
    """Rename a non-main room and audit the change."""
    if room == "pack-main":
        raise ValueError("the main room cannot be renamed")
    _validate_room_name(new_name)
    connection = context.connect()
    try:
        row = connection.execute(
            "SELECT id FROM rooms WHERE name=?", (room,)
        ).fetchone()
        if not row:
            raise ValueError(f"no such room: {room}")
        try:
            connection.execute(
                "UPDATE rooms SET name=? WHERE id=?", (new_name, row["id"])
            )
        except sqlite3.IntegrityError as error:
            raise ValueError(f"room already exists: {new_name}") from error
        context.audit(
            connection,
            member["name"],
            "room_rename",
            f"room {row['id']}: {room} -> {new_name}",
        )
        connection.commit()
    finally:
        connection.close()
    return {"room_id": row["id"], "name": new_name}


def archive_room(context, member, room: str, archived: bool = True) -> dict[str, Any]:
    """Archive or restore a room without deleting its messages or tasks."""
    if room == "pack-main":
        raise ValueError("the main room cannot be archived")
    connection = context.connect()
    try:
        row = connection.execute(
            "SELECT id FROM rooms WHERE name=?", (room,)
        ).fetchone()
        if not row:
            raise ValueError(f"no such room: {room}")
        connection.execute(
            "UPDATE rooms SET archived_at=? WHERE id=?",
            (context.now() if archived else None, row["id"]),
        )
        context.audit(
            connection,
            member["name"],
            "room_archive" if archived else "room_unarchive",
            f"room {row['id']}: {room}",
        )
        connection.commit()
    finally:
        connection.close()
    return {"room_id": row["id"], "name": room, "archived": bool(archived)}


def set_room_summary(context, member, room: str, summary: str, validate_summary) -> dict[str, Any]:
    """Validate and atomically replace the room summary."""
    data = validate_summary(summary)
    connection = context.connect()
    try:
        row = connection.execute(
            "SELECT id FROM rooms WHERE name=?", (room,)
        ).fetchone()
        if not row:
            raise ValueError(f"no such room: {room}")
        now = context.now()
        connection.execute(
            "INSERT INTO room_summaries(room_id,data,updated_at,updated_by) VALUES(?,?,?,?)"
            " ON CONFLICT(room_id) DO UPDATE SET data=excluded.data,"
            " updated_at=excluded.updated_at, updated_by=excluded.updated_by",
            (row["id"], json.dumps(data, ensure_ascii=False), now, member["name"]),
        )
        context.audit(
            connection,
            member["name"],
            "room_summary_set",
            f"room {row['id']}: {room} decided={len(data['decided'])}"
            f" in_progress={len(data['in_progress'])} pending={len(data['pending'])}",
        )
        connection.commit()
    finally:
        connection.close()
    return {"room": room, "summary": data, "updated_at": now, "updated_by": member["name"]}


def _message_for_author(conn, member, message_id: int, operation: str):
    """Check author, external room, structural kind and deletion state."""
    message = conn.execute(
        "SELECT * FROM messages WHERE id=?", (message_id,)
    ).fetchone()
    if not message:
        raise ValueError(f"no such message: {message_id}")
    if message["author_id"] != member["id"]:
        raise ValueError(f"only the author can {operation} this message")
    if ((member.get("kind") or "") == "external"
            and message["room_id"] != member["bound_room_id"]):
        raise ValueError("forbidden: message is outside the bound room")
    if message["kind"] != "chat":
        raise ValueError(
            f"only chat messages can be {operation}d (kind={message['kind']})"
        )
    if message["deleted_at"] is not None:
        raise ValueError(f"message {message_id} already deleted")
    return message


def delete_message(context, member, message_id: int) -> dict[str, Any]:
    """Soft-delete one's own chat without retaining its former body."""
    conn = context.connect()
    try:
        message = _message_for_author(conn, member, message_id, "delete")
        changed = conn.execute(
            "UPDATE messages SET body=?, deleted_at=? "
            "WHERE id=? AND author_id=? AND kind='chat' "
            "AND deleted_at IS NULL",
            (DELETED_PLACEHOLDER, context.clock(), message_id, member["id"]),
        )
        if changed.rowcount != 1:
            raise ValueError(f"message {message_id} already deleted")
        context.audit(
            conn, member["name"], "message_delete", f"message {message_id}"
        )
        conn.commit()
        return {"message_id": message_id, "deleted": True}
    finally:
        conn.close()


def edit_message(context, member, message_id: int,
                 new_body: str) -> dict[str, Any]:
    """Edit one's own undeleted chat, preserving id-only audit entries."""
    if not new_body or not new_body.strip():
        raise ValueError("new_body must not be empty")
    if len(new_body) > 20000:
        raise ValueError("new_body too long: > 20000 chars")
    conn = context.connect()
    try:
        _message_for_author(conn, member, message_id, "edit")
        changed = conn.execute(
            "UPDATE messages SET body=?, edited_at=? "
            "WHERE id=? AND author_id=? AND kind='chat' "
            "AND deleted_at IS NULL",
            (new_body, context.clock(), message_id, member["id"]),
        )
        if changed.rowcount != 1:
            raise ValueError(f"message {message_id} already deleted")
        context.audit(
            conn, member["name"], "message_edit", f"message {message_id}"
        )
        conn.commit()
        return {"message_id": message_id, "edited": True}
    finally:
        conn.close()
