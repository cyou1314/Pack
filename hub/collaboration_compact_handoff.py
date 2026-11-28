"""Immutable, owner-reviewed compact representation of an approved dependency.

Not a summarizer. The trusted review transaction supplies exact owner text.
Scope is every existing direct downstream edge of this source in the run.
Never overwrite the original result or invent a new dependency.
"""
import hashlib
import json

from collaboration_handoff_migration import validate_handoff_extension
from collaboration_store import _require_write, _time

PREFIX = "[Owner-approved compact handoff; original retained in Pack]\n"


def _encoded_size(text):
    return len(json.dumps(text,ensure_ascii=False,separators=(",",":")).encode("utf-8"))


def _wire_text(text, original):
    from collaboration_context import MAX_BYTES
    if (not isinstance(text,str) or not text.strip()
            or len(text.encode("utf-8"))>MAX_BYTES):
        raise ValueError("invalid compact handoff text")
    wire = PREFIX + text
    if _encoded_size(wire)>=_encoded_size(original):
        raise ValueError("compact handoff must reduce encoded text bytes")
    return wire


def compact_dependency(conn, *, task_id, owner_id, original, source_sha256):
    """Called only after the normal dependency provenance checks succeed."""
    if not validate_handoff_extension(conn):
        return original,source_sha256
    row = conn.execute(
        "SELECT owner_id,source_sha256,text,approved_at FROM collaboration_compact_handoffs WHERE task_id=?",
        (task_id,)).fetchone()
    if row is None:
        return original,source_sha256
    if row[0]!=owner_id or row[1]!=source_sha256:
        raise ValueError("compact handoff original source changed")
    wire = _wire_text(row[2],original)
    digest = hashlib.sha256(json.dumps(
        ["compact_handoff_v1",task_id,owner_id,source_sha256,wire,row[3]],
        ensure_ascii=False,separators=(",",":")).encode("utf-8")).hexdigest()
    return wire,digest


def record_approved_handoff(conn, *, run_id, task_id, actor_id, text, now):
    """After approval/reconciliation, before dispatch, same write transaction."""
    from collaboration_context import _owner, _source
    _require_write(conn)
    _time(now)
    _owner(conn,actor_id)
    if not validate_handoff_extension(conn):
        raise ValueError("compact handoff extension unavailable")
    row = conn.execute("""
        SELECT r.room_id,l.step_key,q.owner_id,r.deadline
        FROM collaboration_task_links l
        JOIN collaboration_runs r ON r.id=l.run_id
        JOIN collaboration_requests q ON q.run_id=r.id
        JOIN collaboration_steps s ON s.run_id=l.run_id AND s.step_key=l.step_key
        JOIN tasks t ON t.id=l.task_id
        WHERE l.run_id=? AND l.task_id=? AND r.status='active'
          AND s.status='succeeded' AND t.status='done' AND t.envelope IS NULL
        """,(run_id,task_id)).fetchone()
    if not row or row[2]!=actor_id or row[3]<=now:
        raise ValueError("compact handoff source unavailable")
    downstream = conn.execute("""
        SELECT d.step_key,s.status,
            EXISTS(SELECT 1 FROM collaboration_context_grants g
                   WHERE g.run_id=d.run_id AND g.step_key=d.step_key),
            EXISTS(SELECT 1 FROM collaboration_task_links l
                   WHERE l.run_id=d.run_id AND l.step_key=d.step_key)
        FROM collaboration_dependencies d
        JOIN collaboration_steps s ON s.run_id=d.run_id AND s.step_key=d.step_key
        WHERE d.run_id=? AND d.prerequisite=? ORDER BY d.step_key
        """,(run_id,row[1])).fetchall()
    if not downstream or any(tuple(d[1:])!=("pending",0,0) for d in downstream):
        raise ValueError("compact handoff requires undispatched direct dependents")
    original,digest = _source(conn,run_id=run_id,step_key=downstream[0][0],
        room=row[0],owner_id=actor_id,kind="dependency_result",source_id=task_id,
        use_handoff=False)
    _wire_text(text,original)
    if conn.execute("SELECT 1 FROM collaboration_compact_handoffs WHERE task_id=?",(task_id,)).fetchone():
        raise ValueError("compact handoff already recorded")
    conn.execute("INSERT INTO collaboration_compact_handoffs(task_id,owner_id,source_sha256,text,approved_at) VALUES(?,?,?,?,?)",
                 (task_id,actor_id,digest,text,now))


def inspect_handoff(conn, *, task_id, owner_id):
    """Owner-only readback, caller already scoped task and authenticated owner."""
    if not validate_handoff_extension(conn):
        return None
    row = conn.execute("SELECT text,source_sha256 FROM collaboration_compact_handoffs WHERE task_id=? AND owner_id=?",
                       (task_id,owner_id)).fetchone()
    return None if row is None else {"text":row[0],"original_source_sha256":row[1],
                                    "wire_text":PREFIX+row[0]}
