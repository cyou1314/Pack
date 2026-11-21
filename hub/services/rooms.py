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
