"""Memory write service with cross-process quota and version atomicity."""
from __future__ import annotations

import json


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
