"""Experimental explicit connection binding, not a live Hub endpoint.

Only a trusted authenticated host may call these helpers. A binding records
registry metadata, not proof of a Provider login or authority to dispatch.
No production migration or automatic activation is performed.
"""
from collaboration_context import _owner, require_executor
from collaboration_identity_inventory import _PROVIDER_MODE, _account_uuid
from collaboration_store import _require_write

# Proposed additive table. The integration migration must be separately
# reviewed after the Foundation Registry lands; never edit existing checksums.
SCHEMA = """
CREATE TABLE collaboration_connection_bindings (
    member_id INTEGER PRIMARY KEY REFERENCES collaboration_executors(member_id) ON DELETE RESTRICT,
    owner_id INTEGER NOT NULL REFERENCES members(id) ON DELETE RESTRICT,
    account_ref TEXT NOT NULL UNIQUE,
    agent_id TEXT NOT NULL UNIQUE REFERENCES agents(agent_id) ON DELETE RESTRICT,
    connection_id TEXT NOT NULL UNIQUE REFERENCES agent_connections(connection_id) ON DELETE RESTRICT,
    revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1))
)
"""


def _transaction(conn):
    _require_write(conn)
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise RuntimeError("binding requires foreign key enforcement")


def _validate(conn, member_id, actor_id, account_ref, agent_id, connection_id):
    if (type(member_id) is not int or member_id <= 0
            or type(actor_id) is not int or actor_id <= 0
            or not isinstance(account_ref, str)
            or _account_uuid(account_ref) != account_ref
            or any(not isinstance(x, str) or not x.strip() for x in (agent_id, connection_id))):
        raise ValueError("invalid connection binding parameters")
    _owner(conn, actor_id)
    executor = require_executor(conn, member_id)
    if executor[0] != actor_id or executor[3] != account_ref:
        raise ValueError("executor ownership or account mismatch")
    # Reject duplicate identities even if another executor was revoked.
    # No account reference is automatically recycled.
    count = conn.execute(
        "SELECT COUNT(*) FROM collaboration_executors WHERE lower(account_ref)=?",
        (account_ref,)
    ).fetchone()[0]
    if count != 1:
        raise ValueError("ambiguous executor account")
    expected = _PROVIDER_MODE.get((executor[1], executor[2]))
    if not expected:
        raise ValueError("unsupported provider mode")
    agent = conn.execute("""
        SELECT a.provider_id,a.legacy_member_id,a.status,p.status
        FROM agents a JOIN providers p ON p.provider_id=a.provider_id
        WHERE a.agent_id=?
        """, (agent_id,)).fetchone()
    if not agent or tuple(agent) != (expected[0], member_id, "active", "active"):
        raise ValueError("agent identity or state mismatch")
    connections = conn.execute("""
        SELECT connection_id,mode,status FROM agent_connections
        WHERE agent_id=? AND status IN ('pending','connected')
        """, (agent_id,)).fetchall()
    if len(connections) != 1 or tuple(connections[0]) != (
            connection_id, expected[1], "connected"):
        raise ValueError("connection identity, mode or state mismatch")


def bind_connection(conn, *, actor_id, member_id, account_ref, agent_id, connection_id):
    """Record an explicit binding or return False on exact, valid replay.

    Caller must own a BEGIN IMMEDIATE write transaction and independently
    authenticate the owner and verify the local account/connection provenance.
    """
    _transaction(conn)
    _validate(conn, member_id, actor_id, account_ref, agent_id, connection_id)
    existing = conn.execute("""
        SELECT owner_id,account_ref,agent_id,connection_id,revoked
        FROM collaboration_connection_bindings WHERE member_id=?
        """, (member_id,)).fetchone()
    if existing:
        if tuple(existing) != (actor_id, account_ref, agent_id, connection_id, 0):
            raise ValueError("connection binding is immutable or revoked")
        return False
    conn.execute("""
        INSERT INTO collaboration_connection_bindings
            (member_id,owner_id,account_ref,agent_id,connection_id)
        VALUES(?,?,?,?,?)
        """, (member_id, actor_id, account_ref, agent_id, connection_id))
    return True


def resolve_connection(conn, *, member_id, account_ref):
    """Recheck a recorded mapping in the caller's transaction.

    This is not a task, session, room, credential or egress authorization.
    """
    _transaction(conn)
    if type(member_id) is not int or member_id <= 0:
        raise ValueError("invalid member")
    row = conn.execute("""
        SELECT owner_id,account_ref,agent_id,connection_id,revoked
        FROM collaboration_connection_bindings WHERE member_id=?
        """, (member_id,)).fetchone()
    if not row or row[4] or row[1] != account_ref:
        raise ValueError("connection binding unavailable")
    _validate(conn, member_id, row[0], account_ref, row[2], row[3])
    return row[2], row[3]


def revoke_connection(conn, *, actor_id, member_id):
    """Owner-only irreversible tombstone; never deletes or releases identity."""
    _transaction(conn)
    _owner(conn, actor_id)
    if type(member_id) is not int or member_id <= 0:
        raise ValueError("invalid member")
    row = conn.execute(
        "SELECT owner_id,revoked FROM collaboration_connection_bindings WHERE member_id=?",
        (member_id,)).fetchone()
    if not row or row[0] != actor_id:
        raise ValueError("connection binding unavailable")
    if row[1]:
        return False
    conn.execute("UPDATE collaboration_connection_bindings SET revoked=1 WHERE member_id=?",
                 (member_id,))
    return True
