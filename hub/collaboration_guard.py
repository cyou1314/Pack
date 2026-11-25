"""Opt-in SQLite backstop for the existing Hub tasks table.

Install only after the collaboration ledger and Hub schema exist, on an idle
connection. This is an invariant guard, not authentication or a live dispatcher.
Legacy mutation paths must be adapted before any linked tasks are dispatched.
"""
from collaboration_store import _require_write

GUARDS = """
CREATE TRIGGER IF NOT EXISTS collaboration_task_identity_v1
BEFORE UPDATE OF id,room_id,assignee,created_by,title,body ON tasks
WHEN EXISTS (SELECT 1 FROM collaboration_task_links WHERE task_id=OLD.id)
 AND (NEW.id IS NOT OLD.id OR NEW.room_id IS NOT OLD.room_id
      OR NEW.assignee IS NOT OLD.assignee OR NEW.created_by IS NOT OLD.created_by
      OR NEW.title IS NOT OLD.title OR NEW.body IS NOT OLD.body)
BEGIN
    SELECT RAISE(ABORT,'collaboration: immutable task identity and input');
END;

CREATE TRIGGER IF NOT EXISTS collaboration_task_transition_v1
BEFORE UPDATE OF status,attempts,claim_token,lease_expires ON tasks
WHEN EXISTS (SELECT 1 FROM collaboration_task_links WHERE task_id=OLD.id)
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM collaboration_task_links l
        JOIN collaboration_runs r ON r.id=l.run_id
        JOIN collaboration_steps s ON s.run_id=l.run_id AND s.step_key=l.step_key
        WHERE l.task_id=OLD.id AND r.room_id=NEW.room_id AND l.member_id=NEW.assignee
          AND s.member=CAST(l.member_id AS TEXT)
          AND (
            -- One reserved claim. A legacy claim without a reservation fails.
            (r.status='active' AND s.status='running' AND s.fence IS NOT NULL
             AND OLD.status='open' AND OLD.attempts=0
             AND OLD.claim_token IS NULL AND OLD.lease_expires IS NULL
             AND NEW.status='claimed' AND NEW.attempts=1
             AND NEW.claim_token IS NOT NULL AND length(NEW.claim_token)>0
             AND NEW.lease_expires IS NOT NULL)
            OR
            -- Existing holder may renew its lease; no token replacement.
            (r.status='active' AND s.status='running' AND s.fence IS NOT NULL
             AND OLD.status IN ('claimed','working') AND NEW.status='working'
             AND OLD.attempts=1 AND NEW.attempts=1
             AND OLD.claim_token IS NOT NULL AND NEW.claim_token IS OLD.claim_token
             AND NEW.lease_expires IS NOT NULL)
            OR
            -- Existing task completion still goes through Hub review.
            (r.status='active' AND s.status='running' AND s.fence IS NOT NULL
             AND OLD.status IN ('claimed','working') AND OLD.attempts=1
             AND OLD.claim_token IS NOT NULL
             AND NEW.status='review' AND NEW.attempts=1
             AND NEW.claim_token IS NULL AND NEW.lease_expires IS NULL)
            OR
            (r.status='active' AND s.status='running' AND s.fence IS NOT NULL
             AND OLD.status='review' AND NEW.status='done'
             AND OLD.attempts=1 AND NEW.attempts=1
             AND NEW.claim_token IS NULL AND NEW.lease_expires IS NULL)
            OR
            -- The collaboration ledger must close first, then cancel tasks.
            (r.status IN ('failed','cancelled','expired')
             AND s.status IN ('failed','cancelled')
             AND OLD.status IN ('open','claimed','working','review','blocked')
             AND NEW.status='cancelled' AND NEW.attempts IS OLD.attempts
             AND NEW.claim_token IS NULL AND NEW.lease_expires IS NULL)
          )
    ) THEN RAISE(ABORT,'collaboration: prohibited task transition') END;
END;

CREATE TRIGGER IF NOT EXISTS collaboration_task_delete_v1
BEFORE DELETE ON tasks
WHEN EXISTS (SELECT 1 FROM collaboration_task_links WHERE task_id=OLD.id)
BEGIN
    SELECT RAISE(ABORT,'collaboration: linked task cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS collaboration_task_replace_v1
BEFORE INSERT ON tasks
WHEN EXISTS (SELECT 1 FROM collaboration_task_links WHERE task_id=NEW.id)
BEGIN
    SELECT RAISE(ABORT,'collaboration: linked task cannot be replaced');
END;
"""


def install_task_guards(conn):
    """Explicit opt-in, atomic and idempotent for this schema version."""
    if conn.in_transaction:
        raise RuntimeError("guard installation requires an idle connection")
    required = {"id","room_id","assignee","created_by","title","body","status",
                "attempts","claim_token","lease_expires"}
    actual = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    if not required <= actual:
        raise RuntimeError("incompatible Hub task schema")
    for table in ("collaboration_runs","collaboration_steps","collaboration_task_links"):
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            (table,)).fetchone():
            raise RuntimeError("collaboration ledger not initialized")
    try:
        conn.executescript("BEGIN IMMEDIATE;\n" + GUARDS + "\nCOMMIT;")
    except BaseException:
        conn.rollback()
        raise


def close_linked_tasks(conn, run_id, *, cancel_task):
    """Invoke trusted Hub cancellation for unfinished linked tasks in one transaction.

    cancel_task(conn, task_id) must not commit or open another connection and
    must perform existing claim/run/desktop/audit cleanup. Raise on mismatch
    so the outer write transaction rolls back. Never call a Provider here.
    """
    _require_write(conn)
    row = conn.execute("SELECT status FROM collaboration_runs WHERE id=?", (run_id,)).fetchone()
    if not row or row[0] not in ("failed","cancelled","expired"):
        raise ValueError("run must be stopped before task cleanup")
    rows = conn.execute("""
        SELECT l.task_id,t.status,s.status FROM collaboration_task_links l
        LEFT JOIN tasks t ON t.id=l.task_id
        JOIN collaboration_steps s ON s.run_id=l.run_id AND s.step_key=l.step_key
        WHERE l.run_id=? ORDER BY l.task_id
        """, (run_id,)).fetchall()
    closed = []
    for task_id, status, step_status in rows:
        if status == "done" and step_status == "succeeded":
            continue
        if status == "cancelled" and step_status in ("failed","cancelled"):
            continue
        if status not in ("open","claimed","working","review","blocked") or step_status not in ("failed","cancelled"):
            raise RuntimeError("task and collaboration ledger disagree")
        cancel_task(conn, task_id)
        after = conn.execute("SELECT status,claim_token,lease_expires FROM tasks WHERE id=?",
                             (task_id,)).fetchone()
        if not after or tuple(after) != ("cancelled",None,None):
            raise RuntimeError("task cancellation incomplete")
        closed.append(task_id)
    return tuple(closed)
