"""Read-only task history services; authentication stays in the MCP tool layer."""
from __future__ import annotations

import json


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
