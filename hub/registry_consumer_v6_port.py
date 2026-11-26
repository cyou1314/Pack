"""Foundation v6 consumer schema, offline integration copies only."""
import math

from registry_v1_port import Migration, _REGISTRY_V1
from registry_v2_v3_port import (
    _TASK_AUDIT_V2, _TASK_HOTPATH_V3, _checksum, _check_indexes, _V2_INDEXES,
)
from registry_event_v4_v5_port import (
    _AGENT_EVENT_INBOX_V4, _AGENT_EVENT_GATEWAY_V5, _verify_objects,
)

_AGENT_EVENT_CONSUMER_V6 = Migration(
    version=6,
    name="agent_event_consumer_leases",
    statements=(
        "CREATE INDEX idx_agent_event_inbox_agent_inbox_seq "
        "ON agent_event_inbox(agent_id, inbox_seq)",
        """CREATE TABLE agent_event_consumer_cursors (
            consumer_id TEXT NOT NULL CHECK(length(trim(consumer_id)) BETWEEN 1 AND 80),
            agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE RESTRICT,
            acked_inbox_seq INTEGER NOT NULL DEFAULT 0 CHECK(acked_inbox_seq >= 0),
            updated_at REAL NOT NULL,
            PRIMARY KEY(consumer_id, agent_id)
        )""",
        """CREATE TABLE agent_event_consumer_leases (
            lease_id TEXT NOT NULL PRIMARY KEY CHECK(length(trim(lease_id)) BETWEEN 16 AND 128),
            consumer_id TEXT NOT NULL CHECK(length(trim(consumer_id)) BETWEEN 1 AND 80),
            agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE RESTRICT,
            lease_token_hash TEXT NOT NULL UNIQUE CHECK(length(lease_token_hash)=64),
            from_inbox_seq INTEGER NOT NULL CHECK(from_inbox_seq > 0),
            through_inbox_seq INTEGER NOT NULL CHECK(through_inbox_seq >= from_inbox_seq),
            acked_through_inbox_seq INTEGER NOT NULL DEFAULT 0
                CHECK(acked_through_inbox_seq >= 0 AND acked_through_inbox_seq <= through_inbox_seq),
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL CHECK(expires_at >= created_at),
            completed_at REAL,
            ack_retry_until REAL,
            closed_at REAL,
            status TEXT NOT NULL CHECK(status IN ('active','completed','expired','released')),
            CHECK(ack_retry_until IS NULL OR (completed_at IS NOT NULL AND ack_retry_until >= completed_at)),
            CHECK((status='completed' AND completed_at IS NOT NULL AND ack_retry_until IS NOT NULL)
                OR (status!='completed' AND completed_at IS NULL AND ack_retry_until IS NULL)),
            CHECK((status='active' AND closed_at IS NULL)
                OR (status!='active' AND closed_at IS NOT NULL))
        )""",
        """CREATE UNIQUE INDEX idx_agent_event_consumer_one_active_lease
            ON agent_event_consumer_leases(consumer_id, agent_id)
            WHERE status='active'""",
        """CREATE INDEX idx_agent_event_consumer_leases_expiry
            ON agent_event_consumer_leases(status, expires_at)""",
        """CREATE TABLE agent_event_consumer_lease_items (
            lease_id TEXT NOT NULL REFERENCES agent_event_consumer_leases(lease_id)
                ON DELETE CASCADE,
            inbox_seq INTEGER NOT NULL REFERENCES agent_event_inbox(inbox_seq)
                ON DELETE RESTRICT,
            acked_at REAL,
            PRIMARY KEY(lease_id, inbox_seq)
        )""",
        """CREATE INDEX idx_agent_event_consumer_lease_items_inbox
            ON agent_event_consumer_lease_items(inbox_seq)""",
        """CREATE INDEX idx_agent_event_consumer_lease_items_pending
            ON agent_event_consumer_lease_items(lease_id, inbox_seq)
            WHERE acked_at IS NULL""",
    ),
)



def apply_consumer_v6(conn, *, now):
    """Advance an exact v5 ledger to v6, or validate an existing v6."""
    if conn.in_transaction:
        raise RuntimeError("consumer migration requires an idle connection")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise RuntimeError("consumer migration requires foreign keys")
    if type(now) not in (int, float) or not math.isfinite(now):
        raise ValueError("invalid migration timestamp")
    chain = (_REGISTRY_V1, _TASK_AUDIT_V2, _TASK_HOTPATH_V3,
             _AGENT_EVENT_INBOX_V4, _AGENT_EVENT_GATEWAY_V5,
             _AGENT_EVENT_CONSUMER_V6)
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT version,name,checksum FROM hub_schema_migrations ORDER BY version"
        ).fetchall()
        expected = [(m.version,m.name,_checksum(m)) for m in chain]
        if len(rows) not in (5,6) or [tuple(r) for r in rows] != expected[:len(rows)]:
            raise RuntimeError("unsupported or mismatched Hub migration ledger")
        if [r[0] for r in conn.execute("PRAGMA quick_check")] != ["ok"]:
            raise RuntimeError("database integrity check failed")
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("database foreign key violation")
        for m in (_REGISTRY_V1, _AGENT_EVENT_INBOX_V4, _AGENT_EVENT_GATEWAY_V5):
            _verify_objects(conn, m, applied=True)
        _check_indexes(conn, _V2_INDEXES, required=True)
        _check_indexes(conn, _TASK_HOTPATH_V3.statements, required=True)
        m = _AGENT_EVENT_CONSUMER_V6
        _verify_objects(conn, m, applied=len(rows)==6)
        applied = []
        if len(rows)==5:
            for sql in m.statements:
                conn.execute(sql)
            conn.execute("INSERT INTO hub_schema_migrations VALUES(?,?,?,?)",
                         (m.version,m.name,_checksum(m),float(now)))
            applied.append(m.version)
        _verify_objects(conn,m,applied=True)
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("database foreign key violation")
        conn.commit()
        return applied
    except BaseException:
        conn.rollback()
        raise
