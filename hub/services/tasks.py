"""Read-only task history services; authentication stays in the MCP tool layer."""
from __future__ import annotations


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
