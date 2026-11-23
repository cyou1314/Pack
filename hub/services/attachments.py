"""Attachment read services with file integrity and external-member scoping.

MCP tool signatures, authentication and access-mode gates remain in hub.py.
Attachment upload and filesystem writes are deliberately unchanged in this batch.
"""
from __future__ import annotations

import base64
import hashlib
import os
import stat


def get_attachment(context, member, attachment_id: int, *, attach_path,
                   max_bytes: int) -> dict:
    """Read a content-addressed image after validating access and integrity.

    The size and SHA-256 recorded at upload must match the file actually
    returned. O_NOFOLLOW blocks final-component symlinks and a realpath
    boundary check blocks parent-directory escape. No audit is committed
    for rejected or corrupt reads.
    """
    conn = context.connect()
    try:
        row = conn.execute(
            "SELECT * FROM attachments WHERE id=?", (attachment_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"no such attachment: {attachment_id}")
        if (member.get("kind") or "") == "external":
            if row["room_id"] != member["bound_room_id"]:
                raise ValueError("forbidden: attachment is outside the bound room")
            if row["created_at"] < member["created_at"]:
                raise ValueError("forbidden: attachment predates external member join")

        expected_size = row["size"]
        if expected_size <= 0 or expected_size > max_bytes:
            raise ValueError("attachment integrity check failed")
        path = attach_path(row["sha256"], row["mime"])
        storage_root = os.path.realpath(context.attachment_dir)
        resolved = os.path.realpath(path)
        if os.path.commonpath((storage_root, resolved)) != storage_root:
            raise ValueError("attachment file unavailable")
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError as error:
            raise ValueError("attachment file missing") from error
        except OSError as error:
            raise ValueError("attachment file unavailable") from error
        with os.fdopen(fd, "rb") as file:
            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                raise ValueError("attachment file unavailable")
            data = file.read(max_bytes + 1)
        if len(data) != expected_size or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("attachment integrity check failed")
        context.audit(
            conn, member["name"], "attachment_get",
            f"attachment {attachment_id}",
        )
        conn.commit()
        return {
            "attachment_id": attachment_id, "mime": row["mime"],
            "size": expected_size, "filename": row["original_name"],
            "data_base64": base64.b64encode(data).decode(),
        }
    finally:
        conn.close()


def list_attachments(context, member, room: str, message_id: int = 0) -> dict:
    """List attachment metadata with external room/join-time restrictions."""
    conn = context.connect()
    try:
        room_row = conn.execute(
            "SELECT id FROM rooms WHERE name=?", (room,)
        ).fetchone()
        if not room_row:
            raise ValueError(f"no such room: {room}")
        external = (member.get("kind") or "") == "external"
        if external and room_row["id"] != member["bound_room_id"]:
            raise ValueError(
                "forbidden: external members may only list their bound room"
            )
        query = (
            "SELECT a.id, a.message_id, a.mime, a.size, a.original_name,"
            " a.created_at, mb.name AS uploader FROM attachments a"
            " JOIN members mb ON mb.id=a.uploader_id WHERE a.room_id=?"
        )
        arguments = [room_row["id"]]
        if external:
            query += " AND a.created_at>=?"
            arguments.append(member["created_at"])
        if message_id:
            query += " AND a.message_id=?"
            arguments.append(message_id)
        query += " ORDER BY a.id ASC"
        rows = conn.execute(query, arguments).fetchall()
        return {
            "attachments": [
                {
                    "id": item["id"], "message_id": item["message_id"],
                    "mime": item["mime"], "size": item["size"],
                    "filename": item["original_name"],
                    "uploader": item["uploader"],
                    "created_at": item["created_at"],
                }
                for item in rows
            ]
        }
    finally:
        conn.close()
