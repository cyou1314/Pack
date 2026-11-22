"""Task domain services; MCP registration and authorization stay in hub.py."""
from __future__ import annotations

import json
import secrets


def list_task_transfers(context, task_id: int) -> dict:
    """Return task history from the isolated HubContext database."""
    conn = context.connect()
    t = conn.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not t:
        conn.close()
        raise ValueError(f"no such task: {task_id}")
    rows = conn.execute(
        """SELECT id, task_id, from_member, to_member, reason, actor, created_at,
                  status, accepted_at, expires_at
           FROM task_transfers WHERE task_id=? ORDER BY id ASC""",
        (task_id,)).fetchall()
    conn.close()
    return {"task_id": task_id, "transfers": [dict(x) for x in rows]}


def get_task_audit(context, task_id: int) -> dict:
    """Return task history from the isolated HubContext database."""
    conn = context.connect()
    t = conn.execute(
        """SELECT t.id, t.title, t.status, t.attempts, t.created_at, t.updated_at,
                  cb.name AS created_by, ab.name AS assignee, t.review_note
           FROM tasks t JOIN members cb ON cb.id=t.created_by
           LEFT JOIN members ab ON ab.id=t.assignee WHERE t.id=?""",
        (task_id,)).fetchone()
    if not t:
        conn.close()
        raise ValueError(f"no such task: {task_id}")
    pre = f"task {task_id}"
    arows = conn.execute(
        """SELECT ts, actor, action, detail FROM audit
           WHERE detail=? OR detail LIKE ? OR detail LIKE ?
           ORDER BY id ASC LIMIT 201""",
        (pre, pre + ":%", pre + " %")).fetchall()
    rrows = conn.execute(
        """SELECT r.fence, mb.name AS member, r.status, r.outcome, r.detail,
                  r.started_at, r.ended_at
           FROM runs r JOIN members mb ON mb.id=r.member_id
           WHERE r.task_id=? ORDER BY r.fence ASC""", (task_id,)).fetchall()
    xrows = conn.execute(
        """SELECT from_member, to_member, reason, actor, created_at, status,
                  accepted_at, expires_at
           FROM task_transfers WHERE task_id=? ORDER BY id ASC""",
        (task_id,)).fetchall()
    conn.close()
    events = [dict(r) for r in arows[:200]]
    return {"task": dict(t), "events": events,
            "events_truncated": len(arows) > 200,
            "runs": [dict(r) for r in rrows],
            "transfers": [dict(r) for r in xrows]}


def list_tasks(context, me, status: str = "", assignee_name: str = "",
               offset: int = 0, room: str = "") -> dict:
    """List tasks with actor-bound external filtering and global queue ranks."""
    conn = context.connect()
    try:
        if (me.get("kind") or "") == "external":
            # 件二：external 的 task_list 强制本房且只回 assignee == 本人
            # 的卡（覆盖调用方传入的过滤参数）——未指派卡与他人的卡
            # 一律不回，防把准备派给 Muse 成员的任务正文泄给外部成员。
            assignee_name = me["name"]
            brow = conn.execute("SELECT name FROM rooms WHERE id=?",
                                (me["bound_room_id"],)).fetchone()
            room = brow["name"] if brow else "\x00no-bound-room"
        q = """SELECT t.id, t.title, t.body, t.status, mb.name AS assignee, t.attempts,
                      t.result, t.envelope, t.lease_expires, t.updated_at,
                      t.fail_class, t.next_claim_at, t.sched_at, t.urgent,
                      cr.name AS created_by, t.review_note, rm.name AS room
               FROM tasks t LEFT JOIN members mb ON mb.id=t.assignee
               LEFT JOIN members cr ON cr.id=t.created_by
               JOIN rooms rm ON rm.id=t.room_id WHERE 1=1"""
        args = []
        if status:
            # staging50：status 可传逗号分隔多状态（Gemini 审计建议），一次
            # 取全活跃态供运行时/桥接免翻页；单状态与旧行为逐字一致。
            parts = [s.strip() for s in status.split(",") if s.strip()]
            if len(parts) > 1:
                q += " AND t.status IN (%s)" % ",".join("?" * len(parts))
                args.extend(parts)
            elif parts:
                q += " AND t.status=?"
                args.append(parts[0])
        if assignee_name:
            q += " AND mb.name=?"
            args.append(assignee_name)
        if room:
            q += " AND rm.name=?"
            args.append(room)
        # staging42 / P2：排序口径不变（urgent 置顶 + id 升序，定稿 §15.3），
        # 只加 offset 分页参数，调用方（如 App 任务板）可翻页拉全量
        offset = max(0, int(offset))
        q += " ORDER BY t.urgent DESC, t.id ASC LIMIT 100 OFFSET ?"
        args.append(offset)
        rows = conn.execute(q, args).fetchall()
        # staging54：队列名次一次查询取全量活跃任务在内存里算（防 N+1）：
        # 按 assignee 原值分组（NULL = 未指派池），组内顺序与上方排序
        # 口径逐字一致（urgent DESC, id ASC），逐个计数即 queue_ahead。
        act = conn.execute(
            "SELECT id, assignee FROM tasks"
            " WHERE status IN ('open','claimed','working')"
            " ORDER BY urgent DESC, id ASC").fetchall()
        ahead = {}
        seen = {}
        for a in act:
            g = a["assignee"]
            ahead[a["id"]] = seen.get(g, 0)
            seen[g] = seen.get(g, 0) + 1
    finally:
        conn.close()
    out = []
    for x in rows:
        d = dict(x)
        if d["status"] in ("open", "claimed", "working"):
            d["queue_ahead"] = ahead.get(d["id"])
        else:
            d["queue_ahead"] = None
        raw_env = d.get("envelope")
        if raw_env:
            try:
                d["envelope"] = json.loads(raw_env)
            except Exception:
                d["envelope"] = None
        else:
            d["envelope"] = None
        nca = d.get("next_claim_at")
        d["backoff_remaining_sec"] = int(nca - context.now()) if nca and nca > context.now() else 0
        out.append(d)
    return {"tasks": out}


def create_task(context, actor, room: str, title: str, body: str,
                assignee_name: str = "", urgent: bool = False, *,
                is_executor_pinned) -> dict:
    """Create a task, its room card, and audit record in one transaction."""
    conn = context.connect()
    try:
        row = conn.execute("SELECT id FROM rooms WHERE name=?", (room,)).fetchone()
        if not row:
            raise ValueError(f"no such room: {room}")
        assignee = None
        if assignee_name:
            target = conn.execute(
                "SELECT id, removed_at, kind, bound_room_id FROM members WHERE name=?",
                (assignee_name,),
            ).fetchone()
            if not target:
                raise ValueError(f"no such member: {assignee_name}")
            if target["removed_at"] is not None:
                raise ValueError(f"member is removed: {assignee_name}")
            if (target["kind"] or "") == "external":
                if target["bound_room_id"] != row["id"]:
                    raise ValueError(
                        f"permanent: external member {assignee_name} is bound to another room"
                    )
                if is_executor_pinned(title, body):
                    raise ValueError(
                        f"permanent: executor-pinned tasks cannot be assigned"
                        f" to external member {assignee_name}"
                    )
            assignee = target["id"]
        now = context.clock()
        cursor = conn.execute(
            "INSERT INTO tasks(room_id,title,body,created_by,assignee,status,urgent,"
            "created_at,updated_at) VALUES(?,?,?,?,?,'open',?,?,?)",
            (row["id"], title, body, actor["id"], assignee,
             1 if urgent else 0, now, now),
        )
        task_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO messages(room_id,author_id,kind,body,task_id,created_at)"
            " VALUES(?,?,'task_card',?,?,?)",
            (row["id"], actor["id"], f"任务#{task_id} {title}", task_id, now),
        )
        context.audit(
            conn, actor["name"], "task_create",
            f"task {task_id}: {title}" + (" [urgent]" if urgent else ""),
        )
        conn.commit()
    finally:
        conn.close()
    return {"task_id": task_id, "status": "open",
            "assignee": assignee_name or None, "urgent": bool(urgent)}


def set_task_urgent(context, actor, task_id: int, urgent: bool) -> dict:
    """Update urgency only for nonterminal tasks; repeated updates are audited."""
    conn = context.connect()
    try:
        task = conn.execute(
            "SELECT status, urgent FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        if not task:
            raise ValueError(f"no such task: {task_id}")
        if task["status"] in ("done", "cancelled"):
            raise ValueError(
                f"task {task_id} is {task['status']}, cannot set urgent"
            )
        conn.execute(
            "UPDATE tasks SET urgent=?, updated_at=? WHERE id=?",
            (1 if urgent else 0, context.now(), task_id),
        )
        context.audit(
            conn, actor["name"], "task_set_urgent",
            f"task {task_id} urgent={1 if urgent else 0}",
        )
        conn.commit()
    finally:
        conn.close()
    return {"task_id": task_id, "urgent": bool(urgent)}


def schedule_task(context, actor, room: str, title: str, body: str,
                  member_name: str, at_epoch: float, *,
                  is_executor_pinned) -> dict:
    """Create a scheduled task plus room card and audit in one transaction."""
    now = context.now()
    if not (at_epoch > now):
        raise ValueError(
            f"at_epoch must be in the future: {at_epoch} <= {now:.0f}"
        )
    conn = context.connect()
    try:
        row = conn.execute(
            "SELECT id FROM rooms WHERE name=?", (room,)
        ).fetchone()
        if not row:
            raise ValueError(f"no such room: {room}")
        target = conn.execute(
            "SELECT id, role, removed_at, kind, bound_room_id"
            " FROM members WHERE name=?", (member_name,)
        ).fetchone()
        if not target:
            raise ValueError(f"no such member: {member_name}")
        if target["removed_at"] is not None:
            raise ValueError(f"member is removed: {member_name}")
        if target["role"] != "member" or member_name == "owner":
            raise ValueError(
                f"cannot schedule for {member_name}: not an AI member"
            )
        if (target["kind"] or "") == "external":
            if target["bound_room_id"] != row["id"]:
                raise ValueError(
                    f"permanent: external member {member_name} is bound to another room"
                )
            if is_executor_pinned(title, body):
                raise ValueError(
                    f"permanent: executor-pinned tasks cannot be assigned"
                    f" to external member {member_name}"
                )
        cursor = conn.execute(
            "INSERT INTO tasks(room_id,title,body,created_by,assignee,status,"
            "next_claim_at,sched_at,created_at,updated_at)"
            " VALUES(?,?,?,?,?,'open',?,?,?,?)",
            (row["id"], title, body, actor["id"], target["id"],
             float(at_epoch), float(at_epoch), now, now),
        )
        task_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO messages(room_id,author_id,kind,body,task_id,created_at)"
            " VALUES(?,?,'task_card',?,?,?)",
            (row["id"], actor["id"], f"任务#{task_id} {title}", task_id, now),
        )
        context.audit(
            conn, actor["name"], "task_schedule",
            f"task {task_id} -> {member_name} at {float(at_epoch):.0f}",
        )
        conn.commit()
    finally:
        conn.close()
    return {"task_id": task_id, "status": "open",
            "assignee": member_name, "scheduled_at": float(at_epoch)}


def claim_task(context, member, task_id: int, *, lease_seconds: int,
               is_executor_pinned, desktop_release_for_task) -> dict:
    """Atomically claim an eligible task and release its orphaned desktop lock.

    Caller authenticates the member. The shared context lock must cover the
    external-member checks and UPDATE together, preventing a reassignment or
    executor-pinning TOCTOU window. No claim token is returned on failure.
    """
    conn = context.connect()
    token = secrets.token_urlsafe(24)
    now = context.now()
    try:
        with context.lock:
            if (member.get("kind") or "") == "external":
                task = conn.execute(
                    "SELECT room_id,title,body,assignee FROM tasks WHERE id=?",
                    (task_id,),
                ).fetchone()
                if task:
                    if task["room_id"] != member["bound_room_id"]:
                        raise ValueError("forbidden: task is outside the bound room")
                    if task["assignee"] != member["id"]:
                        raise ValueError(
                            "forbidden: external members may only claim tasks assigned to themselves"
                        )
                    if is_executor_pinned(task["title"], task["body"]):
                        raise ValueError("forbidden: executor-pinned task")
            cursor = conn.execute(
                """UPDATE tasks SET status='claimed', assignee=?, claim_token=?,
                   lease_expires=?, attempts=attempts+1, next_claim_at=NULL,
                   sched_at=NULL, updated_at=?
                   WHERE id=? AND status='open' AND (assignee IS NULL OR assignee=?)
                     AND (next_claim_at IS NULL OR next_claim_at<=?)""",
                (member["id"], token, now + lease_seconds, now,
                 task_id, member["id"], now),
            )
            won = cursor.rowcount == 1
            if won:
                context.audit(conn, member["name"], "task_claim", f"task {task_id}")
                # The previous owner may have left a stale desktop lock.
                desktop_release_for_task(
                    conn, task_id, member["name"], "orphan_on_claim"
                )
            conn.commit()
        task = conn.execute(
            "SELECT status,assignee,next_claim_at FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
    finally:
        # Also close connections on forbidden claims and write/audit failures.
        conn.close()

    if not task:
        raise ValueError(f"no such task: {task_id}")
    if not won:
        if task["status"] == "open" and task["next_claim_at"] and task["next_claim_at"] > now:
            raise ValueError(
                f"claim failed: task {task_id} in backoff until {task['next_claim_at']:.0f} "
                f"(retry_after={task['next_claim_at'] - now:.0f}s)"
            )
        raise ValueError(f"claim failed: task {task_id} status={task['status']}")
    return {
        "task_id": task_id, "status": "claimed",
        "claim_token": token, "lease_expires": now + lease_seconds,
    }
