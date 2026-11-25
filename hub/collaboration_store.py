"""Internal collaboration ledger. Not an MCP endpoint or Provider executor.

All writes must use write_transaction on a Hub-owned SQLite connection.
The future Hub adapter must authenticate the caller, resolve room/source/member
IDs and permissions, and bind task creation to the claim transaction.
No prompts, secrets, private memories or Provider results are stored here.
"""
from contextlib import contextmanager
from dataclasses import asdict
import json
import math
import uuid

from collaboration_policy import plan_collaboration


SCHEMA = """
CREATE TABLE IF NOT EXISTS collaboration_runs (
    id TEXT PRIMARY KEY,
    room_id INTEGER NOT NULL,
    source_message_id INTEGER NOT NULL,
    plan TEXT NOT NULL,
    max_calls INTEGER NOT NULL CHECK(max_calls BETWEEN 1 AND 8),
    calls_used INTEGER NOT NULL DEFAULT 0 CHECK(calls_used BETWEEN 0 AND max_calls),
    deadline REAL NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','succeeded','failed','cancelled','expired')),
    UNIQUE(room_id, source_message_id)
);
CREATE TABLE IF NOT EXISTS collaboration_steps (
    run_id TEXT NOT NULL REFERENCES collaboration_runs(id),
    step_key TEXT NOT NULL,
    member TEXT NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','running','succeeded','failed','cancelled')),
    fence TEXT,
    PRIMARY KEY(run_id, step_key)
);
CREATE TABLE IF NOT EXISTS collaboration_dependencies (
    run_id TEXT NOT NULL,
    step_key TEXT NOT NULL,
    prerequisite TEXT NOT NULL,
    PRIMARY KEY(run_id, step_key, prerequisite),
    FOREIGN KEY(run_id, step_key) REFERENCES collaboration_steps(run_id, step_key),
    FOREIGN KEY(run_id, prerequisite) REFERENCES collaboration_steps(run_id, step_key)
);
CREATE TABLE IF NOT EXISTS collaboration_task_links (
    run_id TEXT NOT NULL,
    step_key TEXT NOT NULL,
    task_id INTEGER NOT NULL UNIQUE,
    member_id INTEGER NOT NULL,
    PRIMARY KEY(run_id, step_key),
    FOREIGN KEY(run_id, step_key) REFERENCES collaboration_steps(run_id, step_key)
);
CREATE INDEX IF NOT EXISTS idx_collaboration_deadline
    ON collaboration_runs(deadline) WHERE status='active';
"""


def initialize(conn):
    """Explicit opt-in schema initialization; never commit a caller's writes."""
    if conn.in_transaction:
        raise RuntimeError("initialize requires an idle connection")
    try:
        conn.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nCOMMIT;")
    except BaseException:
        conn.rollback()
        raise


@contextmanager
def write_transaction(conn):
    """One writer; caller can also create existing Hub tasks in this transaction."""
    if conn.in_transaction:
        raise RuntimeError("nested collaboration transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _require_write(conn):
    if not conn.in_transaction:
        raise RuntimeError("use write_transaction")


def _time(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("invalid timestamp")
    return value


def create_run(conn, *, room_id, source_message_id, deadline, now, actor_role,
               mode, eligible, leader, workers=(), reviewer=None, max_calls=5):
    """Idempotent replay of an identical plan; conflicts are rejected.

    The deadline must be fixed for the source request, not regenerated on retry.
    Membership, message provenance and room ownership must be checked upstream.
    """
    _require_write(conn)
    if any(type(x) is not int or x <= 0 for x in (room_id, source_message_id)):
        raise ValueError("invalid room or message")
    _time(now)
    _time(deadline)
    steps = plan_collaboration(actor_role=actor_role, mode=mode, eligible=eligible,
                               leader=leader, workers=workers, reviewer=reviewer,
                               max_calls=max_calls)
    plan = json.dumps({"mode": mode, "steps": [asdict(s) for s in steps]},
                      sort_keys=True, separators=(",", ":"))
    existing = conn.execute(
        "SELECT id,plan,max_calls,deadline FROM collaboration_runs "
        "WHERE room_id=? AND source_message_id=?", (room_id, source_message_id)
    ).fetchone()
    if existing:
        if tuple(existing[1:]) != (plan, max_calls, deadline):
            raise ValueError("source request conflicts with existing plan")
        return existing[0]
    if deadline <= now:
        raise ValueError("deadline must be in the future")
    run_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO collaboration_runs(id,room_id,source_message_id,plan,max_calls,deadline,status) "
        "VALUES(?,?,?,?,?,?,'active')",
        (run_id, room_id, source_message_id, plan, max_calls, deadline))
    conn.executemany(
        "INSERT INTO collaboration_steps(run_id,step_key,member,role,status) "
        "VALUES(?,?,?,?,'pending')",
        [(run_id, s.key, s.member, s.role) for s in steps])
    conn.executemany(
        "INSERT INTO collaboration_dependencies(run_id,step_key,prerequisite) VALUES(?,?,?)",
        [(run_id, s.key, dep) for s in steps for dep in s.after])
    return run_id


def _stop(conn, run_id, status):
    changed = conn.execute(
        "UPDATE collaboration_runs SET status=? WHERE id=? AND status='active'",
        (status, run_id)).rowcount
    if changed:
        conn.execute(
            "UPDATE collaboration_steps SET status='cancelled',fence=NULL "
            "WHERE run_id=? AND status IN ('pending','running')", (run_id,))
    return bool(changed)


def expire_due(conn, *, now):
    """Logical expiration; not a guarantee that external side effects stopped."""
    _require_write(conn)
    _time(now)
    ids = conn.execute(
        "SELECT id FROM collaboration_runs WHERE status='active' AND deadline<=?",
        (now,)).fetchall()
    for row in ids:
        _stop(conn, row[0], "expired")
    return len(ids)


def _active(conn, run_id, now):
    _time(now)
    row = conn.execute(
        "SELECT status,deadline FROM collaboration_runs WHERE id=?", (run_id,)
    ).fetchone()
    if not row or row[0] != "active":
        return False
    if row[1] <= now:
        _stop(conn, run_id, "expired")
        return False
    return True


def claim_step(conn, run_id, step_key, *, member, eligible, now):
    """Reserve exactly one attempt, return internal fence or None.

    No automatic retry: uncertain external outcomes must be reconciled manually.
    The fence is not a user authentication token and must stay internal.
    """
    _require_write(conn)
    if not _active(conn, run_id, now) or member not in eligible:
        return None
    fence = uuid.uuid4().hex
    changed = conn.execute("""
        UPDATE collaboration_steps SET status='running',fence=?
        WHERE run_id=? AND step_key=? AND member=? AND status='pending'
        AND EXISTS (SELECT 1 FROM collaboration_runs r WHERE r.id=?
                    AND r.status='active' AND r.calls_used<r.max_calls)
        AND NOT EXISTS (
            SELECT 1 FROM collaboration_dependencies d
            LEFT JOIN collaboration_steps p ON p.run_id=d.run_id AND p.step_key=d.prerequisite
            WHERE d.run_id=? AND d.step_key=? AND (p.status IS NULL OR p.status!='succeeded')
        )
        """, (fence, run_id, step_key, member, run_id, run_id, step_key)).rowcount
    if not changed:
        return None
    conn.execute("UPDATE collaboration_runs SET calls_used=calls_used+1 WHERE id=?", (run_id,))
    return fence


def finish_step(conn, run_id, step_key, *, fence, succeeded, now):
    """Reject stale, duplicate or late completions; fail closed on failure.

    Success means the adapter has validated the step outcome, not merely that
    a Provider returned text. Review rejection must be reported as failure.
    """
    _require_write(conn)
    if type(succeeded) is not bool or not isinstance(fence, str) or not fence:
        raise ValueError("invalid completion")
    if not _active(conn, run_id, now):
        return False
    changed = conn.execute(
        "UPDATE collaboration_steps SET status=?,fence=NULL "
        "WHERE run_id=? AND step_key=? AND status='running' AND fence=?",
        ("succeeded" if succeeded else "failed", run_id, step_key, fence)).rowcount
    if not changed:
        return False
    if not succeeded:
        _stop(conn, run_id, "failed")
    elif not conn.execute(
        "SELECT 1 FROM collaboration_steps WHERE run_id=? AND status!='succeeded' LIMIT 1",
        (run_id,)).fetchone():
        conn.execute("UPDATE collaboration_runs SET status='succeeded' WHERE id=?", (run_id,))
    return True


def cancel_run(conn, run_id, *, now):
    """Caller must authorize cancellation. In-flight external work may continue."""
    _require_write(conn)
    return _active(conn, run_id, now) and _stop(conn, run_id, "cancelled")


def snapshot(conn, run_id):
    """Internal metadata only; caller must enforce room read authorization."""
    row = conn.execute(
        "SELECT status,max_calls,calls_used,deadline FROM collaboration_runs WHERE id=?", (run_id,)
    ).fetchone()
    if row is None:
        return None
    steps = conn.execute(
        "SELECT step_key,member,role,status FROM collaboration_steps WHERE run_id=? ORDER BY step_key",
        (run_id,)).fetchall()
    return dict(status=row[0], max_calls=row[1], calls_used=row[2], deadline=row[3],
                steps=[dict(zip(("key","member","role","status"), s)) for s in steps])
