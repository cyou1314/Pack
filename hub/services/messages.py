"""Room posting service with shared approval, attachment and question contracts.

The MCP/Inbox entrypoints retain authentication, mode gates and locking. The
caller supplies original policy callbacks. This module owns the SQLite write
transaction and always closes its connection, including on approval failures.
"""
from __future__ import annotations

import json

if __package__ and "." in __package__:
    from ..core.task_refs import bounded_task_id
else:
    from core.task_refs import bounded_task_id


def post_message(context, me, room: str, body: str, kind: str = "chat",
                    attachment_ids: str = "", reply_to_id: int = 0,
                    payload: str = "", *, task_ref_re, question_body_limit,
                 validate_question_payload, question_state,
                 ext_approval_per_task, ext_approval_per_hour,
                 ext_msg_retry_after, ext_rate_error, approval_gate,
                 connection=None, before_commit=None) -> dict:
    """room_post 本体（身份已由 room_post 解析并传入）。完整性守卫：
    task_card 只能由 task_create 内部落库、decision 只能由
    规则引擎/审查内部落库或调度员客户端代发——成员经 room_post 伪造
    这两类消息一律拒绝（staging21 对抗复检实证的缺口）。
    approval_request 同理设门（staging35，第 1 轮深查 F1）：必须引用
    存在的任务（任务#N）；非调度员还必须是该任务的当前执行者
    （assignee 本人且任务 claimed/working）——否则整笔拒绝、不落库，
    伪造的待批项进不了批准中心、也喂不到 _approval_state。
    staging51：external 另加房间绑定、kind 限制与频控（普通消息三
    窗口硬拒、approval_request 独立硬顶）；external 调用由 room_post
    在 _lock 内进入本函数，保证「计数→落库」串行不越窗。
    staging53：新增 question / question_answer 两 kind（结构化提问
    卡，契约见 docs/history/PORT-QUESTION-BRIEF.md）——question 与 approval_request
    同门（任务#N 引用 + 当前执行者），另加 payload 结构校验与
    「同任务只许一张未答」；question_answer 仅 dispatcher / owner、
    reply_to_id 必须指向本房未答的 question、禁重复答。两 kind 的
    「查→落库」由 room_post 在 _lock 内串行（防并发双发/双答）。
    external 对两 kind 在下方 kind 面直接拒并写审计，本片不放行。"""
    ext = (me.get('kind') or '') == 'external'
    if len(body or "") > 20000:
        raise ValueError("message body too long: > 20000 chars")
    if kind == "task_card":
        raise ValueError("kind task_card is hub-internal")
    if kind == "decision" and me["role"] != "dispatcher":
        raise ValueError("kind decision is dispatcher-only")
    if kind not in ("chat", "approval_request", "decision",
                    "question", "question_answer"):
        # F2 (staging43): kind 白名单——report/system 等中枢内部 kind 与
        # 任意字符串一律拒绝，防视觉冒充（第 1 轮深查 F2 残余项）。
        raise ValueError(f"kind not allowed via room_post: {kind}")
    if kind in ("question", "question_answer") \
            and len(body or "") > question_body_limit:
        raise ValueError(
            f"{kind} body too long: > {question_body_limit} chars")
    owns_connection = connection is None
    conn = context.connect() if owns_connection else connection
    try:
        r = conn.execute("SELECT id FROM rooms WHERE name=?", (room,)).fetchone()
        if not r:
            raise ValueError(f"no such room: {room}")
        if ext:
            if r["id"] != me["bound_room_id"]:
                raise ValueError(
                    "forbidden: external members may only post in their bound room")
            if kind not in ("chat", "approval_request"):
                if kind in ("question", "question_answer"):
                    # staging53：提问面先在内部成员验证，external 本片
                    # 不放行——拒绝同时写审计（与工具面闸门口径一致）。
                    context.audit(conn, me["name"], "external_kind_denied",
                          f"kind={kind} room={room}")
                    conn.commit()
                raise ValueError(f"kind not allowed for external members: {kind}")
        msg_task_id = None
        msg_payload = None
        if kind == "approval_request":
            m = task_ref_re.search(body or "")
            task_id = bounded_task_id(m)
            t = None
            if task_id is not None:
                t = conn.execute(
                    "SELECT assignee, status FROM tasks WHERE id=?", (task_id,)
                ).fetchone()
            if not t:
                raise ValueError("approval_request must reference an existing task")
            if me["role"] != "dispatcher" and (
                    t["assignee"] != me["id"] or t["status"] not in ("claimed", "working")):
                raise ValueError("approval_request only from the member executing that task")
            msg_task_id = task_id
        if kind == "question":
            m = task_ref_re.search(body or "")
            task_id = bounded_task_id(m)
            t = None
            if task_id is not None:
                t = conn.execute(
                    "SELECT id, assignee, status FROM tasks WHERE id=?", (task_id,)
                ).fetchone()
            if not t:
                raise ValueError("question must reference an existing task")
            if me["role"] != "dispatcher" and (
                    t["assignee"] != me["id"] or t["status"] not in ("claimed", "working")):
                raise ValueError("question only from the member executing that task")
            try:
                msg_payload = validate_question_payload(payload)
            except ValueError:
                raise
            q0, a0 = question_state(conn, t["id"])
            if q0 is not None and a0 is None:
                raise ValueError("task already has an unanswered question")
            msg_task_id = t["id"]
        if kind == "question_answer":
            if me["role"] != "dispatcher" and me["name"] != "owner":
                raise ValueError("question_answer is dispatcher/owner-only")
            if not reply_to_id:
                raise ValueError("question_answer requires reply_to_id")
            tgt = conn.execute("SELECT * FROM messages WHERE id=?",
                               (int(reply_to_id),)).fetchone()
            if not tgt or tgt["kind"] != "question":
                raise ValueError("reply_to_id must reference a question message")
            if tgt["room_id"] != r["id"]:
                raise ValueError(
                    "reply_to_id must reference a question in this room")
            dup = conn.execute(
                "SELECT id FROM messages WHERE kind='question_answer'"
                " AND reply_to_id=?", (tgt["id"],)).fetchone()
            if dup:
                raise ValueError("question already answered")
            msg_task_id = tgt["task_id"]
            if msg_task_id is None:
                mq = task_ref_re.search(tgt["body"] or "")
                msg_task_id = bounded_task_id(mq)
        if ext:
            # 频控在落库前判定：普通消息走三窗口（approval_request 不
            # 计入），approval_request 走独立硬顶（单任务 + 单成员每小时）。
            nowx = context.clock()
            if kind == "approval_request":
                m2 = task_ref_re.search(body or "")
                tid2 = bounded_task_id(m2)
                per_task = conn.execute(
                    "SELECT COUNT(*) AS n FROM messages WHERE author_id=?"
                    " AND kind='approval_request' AND task_id=?",
                    (me["id"], tid2)).fetchone()["n"]
                per_hour = conn.execute(
                    "SELECT COUNT(*) AS n, MIN(created_at) AS o FROM messages"
                    " WHERE author_id=? AND kind='approval_request' AND created_at>=?",
                    (me["id"], nowx - 3600)).fetchone()
                cap = None
                if tid2 is not None and per_task >= ext_approval_per_task:
                    cap = ("task", None)
                elif per_hour["n"] >= ext_approval_per_hour:
                    cap = ("member_hour", int(per_hour["o"] + 3600 - nowx) + 1)
                if cap:
                    context.audit(conn, me["name"], "external_approval_cap",
                          f"task={tid2} scope={cap[0]} per_task={per_task}"
                          f" per_hour={per_hour['n']}")
                    conn.commit()
                    raise ValueError(json.dumps(
                        {"error": "approval_request_cap", "scope": cap[0],
                         "retry_after": cap[1]}, ensure_ascii=False))
            else:
                ra = ext_msg_retry_after(conn, me["id"], nowx)
                if ra is not None:
                    context.audit(conn, me["name"], "external_rate_limited",
                          f"kind={kind} retry_after={ra}")
                    conn.commit()
                    raise ext_rate_error(ra)
        cur = conn.execute(
            "INSERT INTO messages(room_id,author_id,kind,body,task_id,created_at,reply_to_id,payload)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (r["id"], me["id"], kind, body, msg_task_id, context.clock(),
             int(reply_to_id) or None, msg_payload))
        mid = cur.lastrowid
        if attachment_ids:
            # 附件绑定（staging39）：与消息同事务；只能绑自己上传的、同房间、
            # 尚未绑定的附件，≤4 个；任一不合规整笔回滚（消息也不落库）。
            try:
                ids = json.loads(attachment_ids)
            except Exception:
                conn.rollback()
                raise ValueError("attachment_ids must be a JSON array of ids")
            if (not isinstance(ids, list) or not ids or len(ids) > 4
                    or any(not isinstance(i, int) or isinstance(i, bool) for i in ids)
                    or len(set(ids)) != len(ids)):
                conn.rollback()
                raise ValueError("attachment_ids must be 1-4 unique integer ids")
            for aid in ids:
                cur2 = conn.execute(
                    "UPDATE attachments SET message_id=? WHERE id=? AND message_id IS NULL AND room_id=? AND uploader_id=?",
                    (mid, aid, r["id"], me["id"]))
                if cur2.rowcount != 1:
                    conn.rollback()
                    raise ValueError(f"attachment {aid} cannot be bound")
        extra = {}
        if kind == "approval_request":
            extra = approval_gate(conn, r["id"], mid, body, poster=me)
        if before_commit is not None:
            before_commit(conn, mid)
        conn.commit()
        out = {"message_id": mid}
        out.update(extra)
        return out
    finally:
        if owns_connection:
            conn.close()


def send_inbox_message(context, member, body: str, reply_to_id: int = 0,
                       attachment_ids: str = "", op_key: str = "",
                       ignore_new: bool = False, room: str = "", *,
                       resolve_room, read_cursor, inbox_msg_sql, inbox_items,
                       blind_preview: int, post_on_connection) -> dict:
    """Atomically send an inbox chat and persist its idempotency receipt.

    BEGIN IMMEDIATE serializes the idempotency lookup and message insert
    across Hub processes, not just threads sharing context.lock.
    The injected poster must use this very connection and invoke the
    before_commit hook after message/attachment writes, before commit.
    """
    room_id, room_name = resolve_room(member, room)
    conn = context.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if op_key:
            prior = conn.execute(
                "SELECT message_id FROM message_op_keys "
                "WHERE member_id=? AND op_key=?",
                (member["id"], op_key),
            ).fetchone()
            if prior is not None and prior["message_id"] is not None:
                return {
                    "sent": True, "message_id": prior["message_id"],
                    "deduplicated": True,
                }

        external = (member.get("kind") or "") == "external"
        floor = (member["joined_msg_id"] or 0) if external else 0
        cursor = read_cursor(conn, member["id"], room_id, floor)
        if reply_to_id:
            target = conn.execute(
                "SELECT id,room_id FROM messages WHERE id=?",
                (int(reply_to_id),),
            ).fetchone()
            if not target or target["room_id"] != room_id:
                raise ValueError(
                    "reply_to_id must reference a message in this room"
                )
            if external and target["id"] <= floor:
                raise ValueError(
                    "reply_to_id is before your join watermark"
                )
        if not ignore_new:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM messages "
                "WHERE room_id=? AND id>? AND author_id<>?",
                (room_id, cursor, member["id"]),
            ).fetchone()["n"]
            if total:
                rows = conn.execute(
                    inbox_msg_sql + " WHERE m.room_id=? AND m.id>?"
                    " AND m.author_id<>? ORDER BY m.id ASC LIMIT ?",
                    (room_id, cursor, member["id"], blind_preview),
                ).fetchall()
                names = [
                    row["name"] for row in
                    conn.execute("SELECT name FROM members").fetchall()
                ]
                items = inbox_items(conn, rows, member, names)
                context.audit(
                    conn, member["name"], "inbox_send_blind_blocked",
                    f"room={room_name} new={total}",
                )
                conn.commit()
                return {
                    "sent": False, "blocked": "new_messages",
                    "new_count": total, "new_messages": items,
                    "cursor": cursor,
                }

        def persist_receipt(connection, message_id):
            if not op_key:
                return
            # Backfill an old nullable receipt if necessary, but never
            # replace a completed receipt with a different message.
            changed = connection.execute(
                "INSERT INTO message_op_keys"
                "(member_id,op_key,message_id,created_at) "
                "VALUES(?,?,?,?) "
                "ON CONFLICT(member_id,op_key) DO UPDATE SET "
                "message_id=excluded.message_id, "
                "created_at=excluded.created_at "
                "WHERE message_op_keys.message_id IS NULL",
                (member["id"], op_key, message_id, context.clock()),
            )
            if changed.rowcount != 1:
                raise ValueError(
                    "op_key has already been used for another message"
                )
            context.audit(
                connection, member["name"], "inbox_send",
                f"room={room_name} msg={message_id} op_key=1",
            )

        result = post_on_connection(
            conn, room_name, body, attachment_ids,
            int(reply_to_id or 0), persist_receipt,
        )
        return {"sent": True, "message_id": result["message_id"]}
    finally:
        conn.close()


def pull_inbox_messages(context, member, since_id: int = -1,
                        room: str = "", *, user_agent,
                        resolve_room, read_cursor, inbox_msg_sql,
                        inbox_items, window_seconds: int,
                        pull_limit: int, checkin_audit_gap: int) -> dict:
    """Pull an inbox page and check in without advancing its read cursor.

    Authentication, agent-version gate and room policy remain at the Hub
    entrypoint. Heartbeat and audit are committed together; a failed pull
    must not leave an unaccounted check-in.
    """
    room_id, room_name = resolve_room(member, room)
    now = context.clock()
    conn = context.connect()
    try:
        conn.execute(
            "UPDATE members SET last_heartbeat=? WHERE id=?",
            (now, member["id"]),
        )
        external = (member.get("kind") or "") == "external"
        floor = (member["joined_msg_id"] or 0) if external else 0
        cursor = read_cursor(conn, member["id"], room_id, floor)
        if since_id is not None and int(since_id) >= 0:
            effective_since = max(int(since_id), floor)
        else:
            effective_since = max(cursor, floor)
        rows = conn.execute(
            inbox_msg_sql + " WHERE m.room_id=? AND m.id>? AND m.created_at>=?"
            " ORDER BY m.id ASC LIMIT ?",
            (room_id, effective_since, now - window_seconds, pull_limit + 1),
        ).fetchall()
        has_more = len(rows) > pull_limit
        rows = rows[:pull_limit]
        all_names = [
            entry["name"] for entry in
            conn.execute("SELECT name FROM members").fetchall()
        ]
        items = inbox_items(conn, rows, member, all_names)
        previous = conn.execute(
            "SELECT MAX(ts) AS t FROM audit WHERE actor=?"
            " AND action='inbox_pull'",
            (member["name"],),
        ).fetchone()["t"]
        context.audit(
            conn, member["name"], "inbox_pull",
            f"room={room_name} n={len(items)} ua={user_agent or '-'}",
        )
        if previous and now - previous < checkin_audit_gap:
            context.audit(
                conn, member["name"], "inbox_checkin_frequent",
                f"room={room_name} gap={int(now - previous)}s",
            )
        conn.commit()
        return {
            "room": room_name, "cursor": cursor,
            "messages": items, "has_more": has_more,
        }
    finally:
        conn.close()


def mark_inbox_read(context, member, up_to_id: int = 0,
                    room: str = "", *, resolve_room, read_cursor) -> dict:
    """Advance the per-member, per-room cursor without cross-process rewind.

    BEGIN IMMEDIATE acquires SQLite's writer reservation before the read
    and UPSERT, so competing Hub processes cannot overwrite a newer cursor.
    The database MAX guard is a second line of defense against regression.
    """
    room_id, room_name = resolve_room(member, room)
    conn = context.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        external = (member.get("kind") or "") == "external"
        floor = (member["joined_msg_id"] or 0) if external else 0
        cursor = read_cursor(conn, member["id"], room_id, floor)
        room_max = conn.execute(
            "SELECT COALESCE(MAX(id),0) AS m FROM messages WHERE room_id=?",
            (room_id,),
        ).fetchone()["m"]
        target = int(up_to_id) if up_to_id else room_max
        new_cursor = max(cursor, min(target, room_max))
        conn.execute(
            "INSERT INTO member_read_cursors"
            "(member_id,room_id,cursor_id) VALUES(?,?,?)"
            " ON CONFLICT(member_id,room_id) DO UPDATE SET"
            " cursor_id=MAX(member_read_cursors.cursor_id,excluded.cursor_id)",
            (member["id"], room_id, new_cursor),
        )
        context.audit(
            conn, member["name"], "inbox_mark_read",
            f"room={room_name} cursor={new_cursor}",
        )
        conn.commit()
        return {"room": room_name, "cursor": new_cursor}
    finally:
        conn.close()
