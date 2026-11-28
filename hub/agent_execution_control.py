"""Default-off, per-bound-agent execution authorization.

Trusted host only. No Provider login, task approval or external execution.
Caller owns BEGIN IMMEDIATE; no commit or network I/O here.
"""
import math

from collaboration_connection_binding import (
    _transaction, resolve_connection, revoke_connection,
)
from collaboration_context import _owner
from registry_binding_migration import validate_control_extension


def _control_schema(conn):
    _transaction(conn)
    if not validate_control_extension(conn):
        raise PermissionError("agent_control_unavailable")


def _binding(conn, member_id):
    if type(member_id) is not int or member_id<=0:
        raise ValueError("invalid member")
    row=conn.execute(
        "SELECT owner_id,account_ref,agent_id,connection_id,revoked "
        "FROM collaboration_connection_bindings WHERE member_id=?",(member_id,)
    ).fetchone()
    if row is None:
        raise PermissionError("agent_binding_unavailable")
    return row


def _record(conn, member_id):
    return conn.execute(
        "SELECT state,generation FROM pack_registry_agent_controls WHERE member_id=?",
        (member_id,)).fetchone()


def _require_owner(conn, actor_id, member_id):
    if type(actor_id) is not int or actor_id<=0:
        raise PermissionError("agent_control_forbidden")
    _owner(conn,actor_id)
    row=_binding(conn,member_id)
    if row[0]!=actor_id:
        raise PermissionError("agent_control_forbidden")
    mode=conn.execute("SELECT access_mode FROM members WHERE id=?",(actor_id,)).fetchone()
    if mode is None or mode[0] not in (None,"full"):
        raise PermissionError("agent_control_forbidden")
    return row


def _audit(conn, actor_id, member_id, state, generation, now):
    # Opaque numeric member ID only: no Provider session or credential data.
    conn.execute("INSERT INTO audit(ts,actor,action,detail) VALUES(?,?,?,?)",
                 (now,str(actor_id),"agent_execution_control",
                  f"member_id={member_id} state={state} generation={generation}"))


def set_execution_state(conn, *, actor_id, member_id, state, now):
    """Owner-authorized transition; same-state replay is read-only.

    The trusted host must authenticate actor_id and independently prove the
    local account/connection provenance before enable. Pause fences new
    dispatches, not delivery of already generated events. Revocation is final.
    """
    _control_schema(conn)
    if state not in ("enabled","paused","revoked"):
        raise ValueError("invalid execution state")
    if type(now) not in (int,float) or not math.isfinite(now):
        raise ValueError("invalid timestamp")
    row=_require_owner(conn,actor_id,member_id)
    current=_record(conn,member_id)
    old=current[0] if current else None
    generation=current[1] if current else 0
    if (row[4] or old=="revoked") and state!="revoked":
        raise PermissionError("agent_execution_revoked")
    if state=="enabled":
        resolve_connection(conn,member_id=member_id,account_ref=row[1])
    if old==state:
        if state=="revoked":
            revoke_connection(conn,actor_id=actor_id,member_id=member_id)
        return {"state":state,"generation":generation,"changed":False}
    if generation>=9223372036854775807:
        raise RuntimeError("agent execution generation exhausted")
    generation+=1
    if state=="revoked":
        revoke_connection(conn,actor_id=actor_id,member_id=member_id)
    conn.execute("""INSERT INTO pack_registry_agent_controls(member_id,state,generation,updated_at)
                    VALUES(?,?,?,?) ON CONFLICT(member_id) DO UPDATE SET
                    state=excluded.state,generation=excluded.generation,updated_at=excluded.updated_at""",
                 (member_id,state,generation,float(now)))
    _audit(conn,actor_id,member_id,state,generation,float(now))
    return {"state":state,"generation":generation,"changed":True}


def require_execution(conn, *, member_id, account_ref, expected_generation=None,
                      feature_enabled=False):
    """Check explicit global opt-in, per-agent control and live binding.

    Returned generation is a local fence, not a lease, credential, task
    approval or permission to call a Provider. Recheck immediately before
    claiming work; future host must manage in-flight operations separately.
    """
    if feature_enabled is not True:
        raise PermissionError("agent_execution_feature_disabled")
    _control_schema(conn)
    row=_binding(conn,member_id)
    current=_record(conn,member_id)
    if row[4] or not current or current[0]!="enabled":
        raise PermissionError("agent_execution_disabled")
    if expected_generation is not None and (
            type(expected_generation) is not int or expected_generation!=current[1]):
        raise PermissionError("agent_execution_stale_generation")
    if row[1]!=account_ref:
        raise PermissionError("agent_binding_unavailable")
    try:
        agent_id,connection_id=resolve_connection(conn,member_id=member_id,account_ref=account_ref)
    except ValueError:
        raise PermissionError("agent_binding_unavailable") from None
    return {"agent_id":agent_id,"connection_id":connection_id,"generation":current[1]}
