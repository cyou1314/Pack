"""Independent review filing with cross-process approval serialization."""
from __future__ import annotations


def record_review(context, reviewer: dict, task_id: int, target_url: str,
                  owner_quote: str, member_desc: str, verdict: str, *,
                  approval_state) -> dict:
    """Record evidence, the decision message, and audit as one transaction.

    BEGIN IMMEDIATE is essential here: a process-local lock alone cannot
    prevent two Hub processes from both observing the same approval as pending.
    """
    if verdict not in ("allow", "deny"):
        raise ValueError("verdict must be 'allow' or 'deny'")
    target_url = target_url.strip()
    owner_quote = owner_quote.strip()
    member_desc = member_desc.strip()
    for label, value in (
        ("target_url", target_url),
        ("owner_quote", owner_quote),
        ("member_desc", member_desc),
    ):
        if not value:
            raise ValueError(f"missing evidence field: {label}")

    with context.lock:
        conn = context.connect()
        try:
            # Reserve the SQLite writer before reading approval state. This
            # makes the pending-check and decision insert atomic across
            # independent HubContext instances and separate Hub processes.
            conn.execute("BEGIN IMMEDIATE")
            task = conn.execute(
                "SELECT * FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            if not task:
                raise ValueError(f"no such task: {task_id}")
            request, decided_by = approval_state(conn, task_id)
            if not request:
                raise ValueError(
                    f"task {task_id} has no approval request on file; nothing to review"
                )
            if decided_by:
                raise ValueError(
                    f"task {task_id} already decided (via {decided_by}); "
                    "review_file only handles undecided approvals"
                )

            now = context.clock()
            cursor = conn.execute(
                "INSERT INTO review_log(task_id,target_url,owner_quote,member_desc,"
                "verdict,reviewer,created_at) VALUES(?,?,?,?,?,?,?)",
                (task_id, target_url, owner_quote, member_desc,
                 verdict, reviewer["name"], now),
            )
            review_id = cursor.lastrowid
            body = (
                f"批准 任务#{task_id}（独立审查）"
                if verdict == "allow"
                else f"拒绝 任务#{task_id}（独立审查）"
            )
            cursor = conn.execute(
                "INSERT INTO messages(room_id,author_id,kind,body,task_id,created_at) "
                "VALUES(?,?,'decision',?,?,?)",
                (task["room_id"], reviewer["id"], body, task_id, now),
            )
            message_id = cursor.lastrowid
            safe_url = target_url.split("?")[0].split("#")[0]
            context.audit(
                conn, "reviewer", f"review_{verdict}",
                f"task {task_id} review#{review_id} url={safe_url}",
            )
            conn.commit()
            return {
                "task_id": task_id, "verdict": verdict,
                "review_id": review_id, "message_id": message_id,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
