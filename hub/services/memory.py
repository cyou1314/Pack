"""Memory read services; authorization policy remains shared with Hub."""
from __future__ import annotations

import json


def read_memory(context, actor: dict, scope: str, key: str = "",
                member: str = "", *, validate_scope_key, resolve_target,
                current_value) -> dict:
    """Read one current memory value without exposing another member's data."""
    conn = context.connect()
    try:
        validate_scope_key(scope, key)
        target = resolve_target(conn, actor, scope, member)
        row = current_value(conn, target, scope, key)
        if not row:
            return {
                "member": target, "scope": scope, "key": key,
                "text": "", "version_id": None,
                "origin": None, "updated_at": None,
            }
        version = conn.execute(
            "SELECT origin FROM member_memory_versions WHERE id=?",
            (row["version_id"],),
        ).fetchone()
        return {
            "member": target, "scope": scope, "key": key,
            "text": row["text"], "version_id": row["version_id"],
            "origin": version["origin"] if version else None,
            "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


def read_memory_versions(context, actor: dict, scope: str, key: str = "",
                         limit: int = 20, before_id: int = 0,
                         include_text: bool = False, member: str = "", *,
                         validate_scope_key, resolve_target, denied) -> dict:
    """Read the immutable version chain, newest first, with cursor pagination."""
    conn = context.connect()
    try:
        validate_scope_key(scope, key)
        target = resolve_target(conn, actor, scope, member)
        if include_text and actor["role"] != "dispatcher":
            denied(conn, actor, target, scope, "include_text")

        limit = max(1, min(int(limit), 100))
        query = (
            "SELECT * FROM member_memory_versions "
            "WHERE member=? AND scope=? AND key=?"
        )
        args = [target, scope, key]
        if before_id:
            query += " AND id<?"
            args.append(before_id)
        query += " ORDER BY id DESC LIMIT ?"
        args.append(limit)

        rows = conn.execute(query, args).fetchall()
        versions = []
        for row in rows:
            item = {
                "version_id": row["id"],
                "author": row["author"],
                "origin": row["origin"],
                "source_refs": json.loads(row["source_refs"]),
                "size": row["size"],
                "created_at": row["created_at"],
            }
            if include_text:
                item["text"] = row["text"]
            versions.append(item)
        return {"versions": versions}
    finally:
        conn.close()
