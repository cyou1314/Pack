"""Internal opt-in bridge to existing Hub task rows; NOT wired to live tools.

Use canonical member IDs (decimal strings) in collaboration plans for this
bridge. The trusted Hub caller must authenticate, validate source ownership
and permissions, and construct the task using the existing task semantics.
Never enable live dispatch before all legacy mutation paths are guarded.
"""
from collaboration_store import _active, _require_write, claim_step, finish_step


def ready_steps(conn, run_id, *, now):
    """Unbound pending steps whose dependencies succeeded; internal only."""
    _require_write(conn)
    if not _active(conn, run_id, now):
        return []
    return [tuple(row) for row in conn.execute("""
        SELECT s.step_key,s.member,s.role FROM collaboration_steps s
        WHERE s.run_id=? AND s.status='pending'
        AND NOT EXISTS (SELECT 1 FROM collaboration_task_links l
                        WHERE l.run_id=s.run_id AND l.step_key=s.step_key)
        AND NOT EXISTS (
            SELECT 1 FROM collaboration_dependencies d
            LEFT JOIN collaboration_steps p ON p.run_id=d.run_id AND p.step_key=d.prerequisite
            WHERE d.run_id=s.run_id AND d.step_key=s.step_key
              AND (p.status IS NULL OR p.status!='succeeded')
        ) ORDER BY s.step_key
        """, (run_id,)).fetchall()]


def _eligible_member(conn, member_id, room_id):
    row = conn.execute(
        "SELECT role,removed_at,kind,bound_room_id FROM members WHERE id=?",
        (member_id,)).fetchone()
    return bool(row and row[0] == "member" and row[1] is None
                and (row[2] != "external" or row[3] == room_id))


def _step_room(conn, run_id, step_key):
    return conn.execute("""
        SELECT r.room_id,s.member,s.status FROM collaboration_runs r
        JOIN collaboration_steps s ON s.run_id=r.id
        WHERE r.id=? AND s.step_key=?
        """, (run_id, step_key)).fetchone()


def _task(conn, task_id):
    return conn.execute(
        "SELECT room_id,assignee,status,attempts FROM tasks WHERE id=?",
        (task_id,)).fetchone()


def bind_task(conn, run_id, step_key, *, task_id, member_id, now):
    """Bind a ready step to a newly created open task in the same transaction.

    No task creation here: the Hub must create its task, task_card message
    and audit entry using its existing rules, then bind or roll back all.
    An identical binding replay returns False; conflicts raise.
    """
    _require_write(conn)
    if any(type(x) is not int or x <= 0 for x in (task_id, member_id)):
        raise ValueError("invalid task or member ID")
    existing = conn.execute(
        "SELECT task_id,member_id FROM collaboration_task_links "
        "WHERE run_id=? AND step_key=?", (run_id, step_key)).fetchone()
    if existing:
        if tuple(existing) != (task_id, member_id):
            raise ValueError("step already bound to a different task")
        return False
    if not any(s[0] == step_key for s in ready_steps(conn, run_id, now=now)):
        raise ValueError("step not ready")
    row = _step_room(conn, run_id, step_key)
    if row[1] != str(member_id) or not _eligible_member(conn, member_id, row[0]):
        raise ValueError("member identity or room mismatch")
    task = _task(conn, task_id)
    if not task or tuple(task) != (row[0], member_id, "open", 0):
        raise ValueError("task must be fresh, assigned and in the run room")
    if conn.execute("SELECT 1 FROM collaboration_task_links WHERE task_id=?",
                    (task_id,)).fetchone():
        raise ValueError("task already linked to another step")
    conn.execute(
        "INSERT INTO collaboration_task_links(run_id,step_key,task_id,member_id) VALUES(?,?,?,?)",
        (run_id, step_key, task_id, member_id))
    return True


def linked_task(conn, task_id):
    """Internal lookup for future guards in claim/retry/transfer/cancel paths."""
    row = conn.execute(
        "SELECT run_id,step_key,member_id FROM collaboration_task_links WHERE task_id=?",
        (task_id,)).fetchone()
    return tuple(row) if row else None


def reserve_claim(conn, task_id, *, member_id, eligible_ids, now):
    """Before normal task_claim UPDATE, reserve its single attempt atomically.

    Return an internal fence or None; a denied reservation must never fall
    back to normal claim. Caller must roll back on a failed task_claim UPDATE.
    """
    _require_write(conn)
    link = linked_task(conn, task_id)
    if not link or type(member_id) is not int or member_id != link[2]:
        return None
    run_id, key, _ = link
    row = _step_room(conn, run_id, key)
    task = _task(conn, task_id)
    if (not row or row[1] != str(member_id)
            or not task or tuple(task) != (row[0], member_id, "open", 0)
            or not _eligible_member(conn, member_id, row[0])
            or member_id not in eligible_ids):
        return None
    return claim_step(conn, run_id, key, member=str(member_id),
                      eligible={str(member_id)}, now=now)


def reconcile_terminal(conn, task_id, *, now):
    """After trusted task transition, reconcile its actual terminal state.

    A task in review is not a successful step. Reopened/blocked tasks with
    an attempt already spent fail closed; no automatic re-claim is allowed.
    Caller must cancel other linked unfinished tasks using existing Hub
    cancellation/lease/desktop cleanup in the same transaction.
    """
    _require_write(conn)
    link = linked_task(conn, task_id)
    if not link:
        return False
    run_id, key, member_id = link
    row = conn.execute(
        "SELECT fence,status FROM collaboration_steps WHERE run_id=? AND step_key=?",
        (run_id, key)).fetchone()
    room = _step_room(conn, run_id, key)
    task = _task(conn, task_id)
    if not row or row[1] != "running" or not row[0] or not room:
        return False
    if (not task or task[0] != room[0] or task[1] != member_id
            or task[3] != 1 or not _eligible_member(conn, member_id, room[0])):
        return finish_step(conn, run_id, key, fence=row[0], succeeded=False, now=now)
    if task[2] in ("claimed", "working", "review"):
        return False
    return finish_step(conn, run_id, key, fence=row[0],
                       succeeded=task[2] == "done", now=now)
