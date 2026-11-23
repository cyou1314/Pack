"""Attachment read services with file integrity and external-member scoping.

MCP tool signatures, authentication and access-mode gates remain in hub.py.
Upload uses the same SQLite writer transaction for quota checks and metadata.
"""
from __future__ import annotations

import base64
import hashlib
import os
import stat
import tempfile


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


def _verify_existing_upload(path: str, data: bytes, root: str) -> None:
    """Reject symlinks, tampered files and non-regular destinations."""
    resolved_root = os.path.realpath(root)
    if os.path.commonpath((resolved_root, os.path.realpath(path))) != resolved_root:
        raise ValueError("attachment file unavailable")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise ValueError("attachment file unavailable") from error
    with os.fdopen(fd, "rb") as file:
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
            raise ValueError("attachment file unavailable")
        existing = file.read(len(data) + 1)
    if existing != data:
        raise ValueError("attachment integrity check failed")


def _publish_upload(context, path: str, data: bytes) -> bool:
    """Publish via a unique 0600 staging file and an exclusive hard link.

    Returns True only when this call creates the final content-addressed
    path. An existing hash path is verified but never overwritten. The
    staging path is removed on success and on all ordinary failure paths.
    """
    root = os.path.abspath(context.attachment_dir)
    destination = os.path.abspath(path)
    if os.path.commonpath((root, destination)) != root:
        raise ValueError("attachment file unavailable")
    if os.path.islink(root):
        raise ValueError("attachment file unavailable")
    directory = os.path.dirname(destination)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    if os.path.realpath(directory) != directory or os.path.islink(directory):
        raise ValueError("attachment file unavailable")
    if os.path.lexists(destination):
        _verify_existing_upload(destination, data, root)
        return False

    fd, temporary_path = tempfile.mkstemp(prefix=".pack-upload-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        try:
            os.link(temporary_path, destination)
            return True
        except FileExistsError:
            # A separate process may have published the same content.
            _verify_existing_upload(destination, data, root)
            return False
    finally:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass


def put_attachment(context, member, room: str, filename: str, mime: str,
                   data_base64: str, *, mime_extensions, max_bytes: int,
                   room_quota: int, external_max_bytes: int,
                   external_daily_uploads: int, external_member_quota: int,
                   sniff_image_mime, attach_path) -> dict:
    """Upload a verified image with serialized quota/accounting decisions.

    The database writer reservation protects the room and external-member
    quotas across processes. Files are never overwritten; failed metadata
    transactions remove only new, unreferenced files created by this call.
    """
    if mime not in mime_extensions:
        raise ValueError(f"unsupported mime: {mime}")
    if len(data_base64) > (max_bytes * 4 // 3) + 1024:
        raise ValueError("attachment too large")
    try:
        data = base64.b64decode(data_base64, validate=True)
    except Exception as error:
        raise ValueError("invalid base64") from error
    if not data:
        raise ValueError("empty attachment")
    if len(data) > max_bytes:
        raise ValueError("attachment too large")

    external = (member.get("kind") or "") == "external"
    if external and len(data) > external_max_bytes:
        audit_connection = context.connect()
        try:
            context.audit(
                audit_connection, member["name"], "external_attachment_quota",
                f"room {room} size {len(data)}:"
                " single file cap exceeded (5MB)",
            )
            audit_connection.commit()
        finally:
            audit_connection.close()
        raise ValueError("attachment too large for external members: > 5MB")
    if sniff_image_mime(data) != mime:
        raise ValueError("mime does not match file content")

    sha = hashlib.sha256(data).hexdigest()
    safe_name = (filename or "").replace("/", "_").replace("\\", "_")[:120]
    # Maintain the existing per-process serialization; BEGIN IMMEDIATE adds
    # protection when different Hub processes use independent locks.
    with context.lock:
        conn = context.connect()
        created_path = None
        existing_reference = False
        try:
            conn.execute("BEGIN IMMEDIATE")
            room_row = conn.execute(
                "SELECT id FROM rooms WHERE name=?", (room,)
            ).fetchone()
            if not room_row:
                raise ValueError(f"no such room: {room}")
            if external:
                if room_row["id"] != member["bound_room_id"]:
                    raise ValueError(
                        "forbidden: external members may only upload to their bound room"
                    )
                daily_count = conn.execute(
                    "SELECT COUNT(*) AS n FROM attachments"
                    " WHERE uploader_id=? AND created_at>=?",
                    (member["id"], context.clock() - 86400),
                ).fetchone()["n"]
                member_bytes = conn.execute(
                    "SELECT COALESCE(SUM(size),0) AS s FROM attachments"
                    " WHERE uploader_id=?", (member["id"],),
                ).fetchone()["s"]
                reason = None
                if daily_count >= external_daily_uploads:
                    reason = f"daily upload cap reached ({external_daily_uploads}/day)"
                elif member_bytes + len(data) > external_member_quota:
                    reason = "member attachment quota exceeded (50MB total)"
                if reason:
                    context.audit(
                        conn, member["name"], "external_attachment_quota",
                        f"room {room} size {len(data)}: {reason}",
                    )
                    conn.commit()
                    raise ValueError(f"external attachment quota: {reason}")
            total_bytes = conn.execute(
                "SELECT COALESCE(SUM(size),0) AS s FROM attachments WHERE room_id=?",
                (room_row["id"],),
            ).fetchone()["s"]
            if total_bytes + len(data) > room_quota:
                raise ValueError("room attachment quota exceeded")

            path = attach_path(sha, mime)
            existing_reference = conn.execute(
                "SELECT 1 FROM attachments WHERE sha256=? AND mime=? LIMIT 1",
                (sha, mime),
            ).fetchone() is not None
            if _publish_upload(context, path, data):
                created_path = path
            cursor = conn.execute(
                "INSERT INTO attachments(sha256,mime,size,original_name,"
                "uploader_id,room_id,message_id,created_at)"
                " VALUES(?,?,?,?,?,?,NULL,?)",
                (sha, mime, len(data), safe_name, member["id"],
                 room_row["id"], context.clock()),
            )
            attachment_id = cursor.lastrowid
            context.audit(
                conn, member["name"], "attachment_put",
                f"attachment {attachment_id} room {room} size {len(data)}",
            )
            conn.commit()
            return {
                "attachment_id": attachment_id, "sha256": sha,
                "size": len(data), "mime": mime,
            }
        except Exception:
            # Still holding SQLite's write reservation: no competing uploader
            # can insert a reference while an unreferenced new file is removed.
            if created_path is not None and not existing_reference:
                try:
                    os.unlink(created_path)
                except FileNotFoundError:
                    pass
            raise
        finally:
            conn.close()
