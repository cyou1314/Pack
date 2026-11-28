"""Foundation v4/v5 event schema port, for disposable integration databases.

Preserves canonical DDL and migration checksums. No gateway routes, token
issuance or event delivery are activated by this module.
"""
import math
import re
import sqlite3

from registry_v1_port import Migration, _REGISTRY_V1
from registry_v2_v3_port import (
    _TASK_AUDIT_V2, _TASK_HOTPATH_V3, _checksum, _check_indexes, _V2_INDEXES,
)

_AGENT_EVENT_INBOX_V4 = Migration(
    version=4,
    name="agent_event_inbox",
    statements=(
        """CREATE TABLE agent_event_inbox (
            inbox_seq INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE RESTRICT,
            event_id TEXT NOT NULL,
            provider_id TEXT NOT NULL REFERENCES providers(provider_id) ON DELETE RESTRICT,
            event_type TEXT NOT NULL,
            event_sequence INTEGER NOT NULL CHECK(event_sequence >= 0),
            occurred_at TEXT NOT NULL,
            event_json TEXT NOT NULL,
            received_at REAL NOT NULL,
            processed_at REAL,
            process_error_code TEXT,
            UNIQUE(agent_id, event_id)
        )""",
        "CREATE INDEX idx_agent_event_inbox_pending "
        "ON agent_event_inbox(processed_at, inbox_seq)",
        "CREATE INDEX idx_agent_event_inbox_agent_sequence "
        "ON agent_event_inbox(agent_id, event_sequence, inbox_seq)",
    ),
)


_AGENT_EVENT_GATEWAY_V5 = Migration(
    version=5,
    name="agent_event_gateway_credentials",
    statements=(
        """CREATE TABLE agent_event_pair_codes (
            code_id INTEGER PRIMARY KEY,
            agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE RESTRICT,
            code_hash TEXT NOT NULL UNIQUE,
            created_by TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            redeemed_at REAL,
            revoked_at REAL,
            fail_count INTEGER NOT NULL DEFAULT 0 CHECK(fail_count >= 0),
            locked_at REAL
        )""",
        """CREATE UNIQUE INDEX idx_agent_event_pair_one_active
           ON agent_event_pair_codes(agent_id)
           WHERE redeemed_at IS NULL AND revoked_at IS NULL AND locked_at IS NULL""",
        "CREATE INDEX idx_agent_event_pair_agent_created "
        "ON agent_event_pair_codes(agent_id, created_at DESC)",
        """CREATE TABLE agent_event_tokens (
            token_id INTEGER PRIMARY KEY,
            agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE RESTRICT,
            token_hash TEXT NOT NULL UNIQUE,
            scope TEXT NOT NULL CHECK(scope='events:write'),
            created_by TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            revoked_at REAL,
            last_used_at REAL
        )""",
        "CREATE INDEX idx_agent_event_tokens_agent_active "
        "ON agent_event_tokens(agent_id, revoked_at, expires_at)",
        """CREATE TABLE agent_event_pair_attempts (
            id INTEGER PRIMARY KEY,
            ts REAL NOT NULL,
            success INTEGER NOT NULL DEFAULT 0 CHECK(success IN (0,1))
        )""",
        "CREATE INDEX idx_agent_event_pair_attempts_ts "
        "ON agent_event_pair_attempts(ts)",
        "CREATE INDEX idx_agent_event_inbox_agent_received "
        "ON agent_event_inbox(agent_id, received_at, inbox_seq)",
    ),
)





from registry_ddl_compare import normalize_ddl as _normalize


def _objects(migration):
    for sql in migration.statements:
        match = re.match(r"CREATE (?:UNIQUE )?(TABLE|INDEX) (\w+)", sql)
        if not match:
            raise RuntimeError("unexpected event migration DDL")
        yield match[1].lower(), match[2], sql


def _verify_objects(conn, migration, *, applied):
    for kind, name, sql in _objects(migration):
        row = conn.execute(
            "SELECT type,sql FROM sqlite_master WHERE name=?", (name,)
        ).fetchone()
        if not applied:
            if row is not None:
                raise RuntimeError("unversioned event object: " + name)
        elif (row is None or row[0] != kind or not row[1]
              or _normalize(row[1]) != _normalize(sql)):
            raise RuntimeError("missing or incompatible event object: " + name)


def apply_event_v4_v5(conn, *, now):
    """Apply v4/v5 together after v3, or finish a valid v4-only database.

    Caller owns an offline copy. Existing ledger must be an exact contiguous
    prefix; foreign keys, integrity and prior index definitions are checked.
    No migration is applied to a running Hub by this helper.
    """
    if conn.in_transaction:
        raise RuntimeError("event migration requires an idle connection")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise RuntimeError("event migration requires foreign keys")
    if type(now) not in (int, float) or not math.isfinite(now):
        raise ValueError("invalid migration timestamp")
    chain = (_REGISTRY_V1, _TASK_AUDIT_V2, _TASK_HOTPATH_V3,
             _AGENT_EVENT_INBOX_V4, _AGENT_EVENT_GATEWAY_V5)
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT version,name,checksum FROM hub_schema_migrations ORDER BY version"
        ).fetchall()
        expected = [(m.version, m.name, _checksum(m)) for m in chain]
        if not 3 <= len(rows) <= 5 or [tuple(row) for row in rows] != expected[:len(rows)]:
            raise RuntimeError("unsupported or mismatched Hub migration ledger")
        if [r[0] for r in conn.execute("PRAGMA quick_check")] != ["ok"]:
            raise RuntimeError("database integrity check failed")
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("database foreign key violation")
        _verify_objects(conn, _REGISTRY_V1, applied=True)
        _check_indexes(conn, _V2_INDEXES, required=True)
        _check_indexes(conn, _TASK_HOTPATH_V3.statements, required=True)
        for m in chain[3:]:
            _verify_objects(conn, m, applied=m.version <= len(rows))
        applied = []
        for m in chain[len(rows):]:
            for sql in m.statements:
                conn.execute(sql)
            conn.execute(
                "INSERT INTO hub_schema_migrations(version,name,checksum,applied_at) VALUES(?,?,?,?)",
                (m.version, m.name, _checksum(m), float(now)))
            applied.append(m.version)
        for m in chain[3:]:
            _verify_objects(conn, m, applied=True)
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("database foreign key violation")
        conn.commit()
        return applied
    except BaseException:
        conn.rollback()
        raise
