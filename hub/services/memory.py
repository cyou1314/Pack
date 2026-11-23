"""Memory services: current reads, immutable history and quota-safe writes."""
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


def write_memory(context, actor: dict, scope: str, key: str,
                 text: str | None, source_refs, origin: str,
                 member: str = "", *,
                 validate_scope_key, resolve_target, denied, normalize_origin,
                 validate_refs, error, measure, current_value, append_version,
                 core_limit_bytes: int, shared_limit_bytes: int,
                 topic_limit_chars: int, max_topics: int,
                 soft_cap_chars: int, limit_hint: str) -> dict:
    """Validate, append a full version, update current state, and audit atomically.

    BEGIN IMMEDIATE protects the private-topic count check across separate Hub
    processes. A process-local lock by itself cannot enforce the hard topic cap
    when more than one Hub instance shares the same SQLite database.
    """
    with context.lock:
        conn = context.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            validate_scope_key(scope, key)
            target = resolve_target(conn, actor, scope, member)
            if scope == "room-shared" and actor["role"] != "dispatcher":
                denied(conn, actor, target, scope, "write-room-shared")
            effective_origin = normalize_origin(actor, origin)
            refs = validate_refs(source_refs)

            if text is None:
                if scope != "private":
                    error(
                        "invalid_argument",
                        "text=null（删主题）只对 scope=private 有效；"
                        "private-core / room-shared 请写空串或改写全文",
                    )
                refs = refs + [{"type": "delete"}]
                version_id, size, _ = append_version(
                    conn, target, scope, key, "", actor["name"],
                    effective_origin, refs,
                )
                context.audit(
                    conn, actor["name"], "memory_write",
                    json.dumps({
                        "member": target, "scope": scope, "key": key,
                        "version_id": version_id, "origin": effective_origin,
                        "size": 0, "deleted": True,
                    }, ensure_ascii=False),
                )
                conn.commit()
                return {
                    "version_id": version_id, "size": 0,
                    "limit": topic_limit_chars, "deleted": True,
                }

            if not isinstance(text, str):
                error(
                    "invalid_argument",
                    "text 必须是字符串（或删主题时传 null）",
                )
            size = measure(scope, text)
            if scope in ("private-core", "room-shared"):
                limit = (
                    core_limit_bytes if scope == "private-core"
                    else shared_limit_bytes
                )
                if size > limit:
                    error(
                        "memory_limit_exceeded", limit_hint,
                        scope=scope, limit_bytes=limit, current_bytes=size,
                    )
            else:
                if size > topic_limit_chars:
                    error(
                        "memory_limit_exceeded", "拆分主题或删旧段落后重试",
                        scope=scope, limit_chars=topic_limit_chars,
                        current_chars=size,
                    )
                current = current_value(conn, target, scope, key)
                is_new_topic = not (current and current["text"] != "")
                if is_new_topic and text != "":
                    count = conn.execute(
                        "SELECT COUNT(*) AS c FROM member_memory "
                        "WHERE member=? AND scope='private' AND text != ''",
                        (target,),
                    ).fetchone()["c"]
                    if count >= max_topics:
                        error(
                            "memory_topic_limit",
                            "合并或删除一个旧主题（删除 = memory_write 同 key 传 text=null）",
                            limit_topics=max_topics,
                        )

            version_id, size, _ = append_version(
                conn, target, scope, key, text, actor["name"],
                effective_origin, refs,
            )
            context.audit(
                conn, actor["name"], "memory_write",
                json.dumps({
                    "member": target, "scope": scope, "key": key,
                    "version_id": version_id, "origin": effective_origin,
                    "size": size,
                }, ensure_ascii=False),
            )
            if scope == "private":
                total = conn.execute(
                    "SELECT COALESCE(SUM(LENGTH(text)),0) AS s "
                    "FROM member_memory WHERE member=? AND scope='private'",
                    (target,),
                ).fetchone()["s"]
                if total > soft_cap_chars:
                    context.audit(
                        conn, "system", "memory_soft_cap",
                        json.dumps({
                            "member": target, "total_chars": total,
                            "soft_cap": soft_cap_chars,
                        }, ensure_ascii=False),
                    )
            conn.commit()
            output_limit = (
                core_limit_bytes if scope == "private-core"
                else shared_limit_bytes if scope == "room-shared"
                else topic_limit_chars
            )
            return {
                "version_id": version_id, "size": size,
                "limit": output_limit,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
