"""Foundation v7 Session projection schema, offline integration only."""
import hashlib
import inspect
import json
import math
import sqlite3

from registry_v1_port import (
    Migration, _REGISTRY_V1, _PROVIDER_LABELS, _provider_for,
    _apply_registry_v1, _normalized_function_source,
)
from registry_task_refs import (
    APPROVAL_TASK_RE, AUDIT_TASK_RE, SQLITE_INT_MAX, bounded_task_id, audit_task_id,
)
from registry_v2_v3_port import (
    _TASK_AUDIT_V2, _TASK_HOTPATH_V3, _apply_task_audit_v2, _check_indexes, _V2_INDEXES,
)
from registry_event_v4_v5_port import (
    _AGENT_EVENT_INBOX_V4, _AGENT_EVENT_GATEWAY_V5, _verify_objects, _normalize,
)
from registry_consumer_v6_port import _AGENT_EVENT_CONSUMER_V6

def _checksum(migration: Migration) -> str:
    payload: dict[str, object] = {
        "version": migration.version,
        "name": migration.name,
        "statements": migration.statements,
    }
    if migration.version == 1:
        # The v1 migration includes a data backfill, not just DDL. Cover the
        # provider mapping and normalized source of every helper that changes
        # backfill behavior. Avoid AST dumps: their representation can differ
        # across supported Python versions and block startup on interpreter upgrades.
        payload["data_migration"] = {
            "provider_labels": _PROVIDER_LABELS,
            "provider_for_source": _normalized_function_source(_provider_for),
            "apply_registry_v1_source": _normalized_function_source(_apply_registry_v1),
        }
    elif migration.version == 2:
        # Freeze the structured audit parser and its backfill implementation.
        payload["data_migration"] = {
            "audit_task_pattern": AUDIT_TASK_RE.pattern,
            "approval_task_pattern": APPROVAL_TASK_RE.pattern,
            "sqlite_int_max": SQLITE_INT_MAX,
            "bounded_task_id_source": _normalized_function_source(bounded_task_id),
            "audit_task_id_source": _normalized_function_source(audit_task_id),
            "apply_task_audit_v2_source": _normalized_function_source(_apply_task_audit_v2),
        }
    elif migration.version == 7:
        # The v7 unique Provider Session binding index is safe only after a
        # read-only preflight. Freeze that preflight implementation in the
        # ledger checksum just like the earlier data-migration helpers.
        payload["data_migration"] = {
            "session_binding_preflight_source": _normalized_function_source(
                _preflight_agent_session_projection_v7
            ),
        }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()



def _preflight_agent_session_projection_v7(connection: sqlite3.Connection) -> None:
    """Fail closed before v7 if legacy Provider Session bindings are ambiguous."""
    rows = connection.execute(
        """SELECT agent_id, provider_session_id FROM agent_sessions
           WHERE provider_session_id IS NOT NULL
           ORDER BY agent_id, session_id"""
    ).fetchall()
    noncanonical = 0
    binding_counts: dict[tuple[str, str], int] = {}
    for row in rows:
        provider_session_id = row["provider_session_id"]
        if (
            not isinstance(provider_session_id, str)
            or not provider_session_id
            or provider_session_id != provider_session_id.strip()
            or len(provider_session_id.strip()) > 160
        ):
            noncanonical += 1
            continue
        key = (row["agent_id"], provider_session_id.strip())
        binding_counts[key] = binding_counts.get(key, 0) + 1

    duplicate_bindings = {
        key: count for key, count in binding_counts.items() if count > 1
    }
    if not noncanonical and not duplicate_bindings:
        return

    affected_agents = sorted({agent_id for agent_id, _ in duplicate_bindings})[:10]
    details = []
    if noncanonical:
        details.append(f"{noncanonical} non-canonical Provider Session ID row(s)")
    if duplicate_bindings:
        details.append(
            f"{len(duplicate_bindings)} duplicate (Agent, Provider Session ID) group(s)"
        )
    if affected_agents:
        details.append("affected Agent IDs: " + ", ".join(affected_agents))
    raise RuntimeError(
        "Hub migration v7 blocked by legacy Session binding preflight: "
        + "; ".join(details)
        + ". Repair only on a verified backup, then rerun the migration."
    )


_AGENT_SESSION_PROJECTION_V7 = Migration(
    version=7,
    name="agent_session_projection_read_models",
    statements=(
        """ALTER TABLE agent_sessions
           ADD COLUMN last_inbox_seq INTEGER NOT NULL DEFAULT 0
           CHECK(last_inbox_seq >= 0)""",
        """CREATE UNIQUE INDEX idx_agent_sessions_agent_provider_session_unique
           ON agent_sessions(agent_id, provider_session_id)
           WHERE provider_session_id IS NOT NULL""",
        """CREATE UNIQUE INDEX idx_agent_event_inbox_seq_agent_unique
           ON agent_event_inbox(inbox_seq, agent_id)""",
        """CREATE TABLE agent_session_messages (
            message_id TEXT PRIMARY KEY
                CHECK(length(trim(message_id)) BETWEEN 1 AND 128),
            session_id TEXT NOT NULL REFERENCES agent_sessions(session_id)
                ON DELETE RESTRICT,
            provider_message_id TEXT NOT NULL
                CHECK(length(trim(provider_message_id)) BETWEEN 1 AND 160),
            provider_run_id TEXT
                CHECK(provider_run_id IS NULL OR
                      length(trim(provider_run_id)) BETWEEN 1 AND 160),
            content TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL CHECK(status IN (
                'streaming','completed','failed','interrupted','incomplete'
            )),
            phase TEXT CHECK(phase IS NULL OR length(phase) <= 64),
            first_inbox_seq INTEGER NOT NULL REFERENCES agent_event_inbox(inbox_seq)
                ON DELETE RESTRICT,
            last_inbox_seq INTEGER NOT NULL REFERENCES agent_event_inbox(inbox_seq)
                ON DELETE RESTRICT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            completed_at REAL,
            UNIQUE(session_id, provider_message_id),
            CHECK(last_inbox_seq >= first_inbox_seq),
            CHECK(length(CAST(content AS BLOB)) <= 4194304),
            CHECK((status='completed' AND completed_at IS NOT NULL)
                OR (status!='completed' AND completed_at IS NULL))
        )""",
        """CREATE INDEX idx_agent_session_messages_session_created
           ON agent_session_messages(session_id, created_at, message_id)""",
        """CREATE INDEX idx_agent_session_messages_session_run
           ON agent_session_messages(session_id, provider_run_id, status)""",
        """CREATE TABLE agent_session_runs (
            run_record_id TEXT PRIMARY KEY
                CHECK(length(trim(run_record_id)) BETWEEN 1 AND 128),
            session_id TEXT NOT NULL REFERENCES agent_sessions(session_id)
                ON DELETE RESTRICT,
            provider_run_id TEXT NOT NULL
                CHECK(length(trim(provider_run_id)) BETWEEN 1 AND 160),
            status TEXT NOT NULL CHECK(status IN (
                'running','completed','failed','interrupted'
            )),
            started_at REAL,
            updated_at REAL NOT NULL,
            ended_at REAL,
            first_inbox_seq INTEGER NOT NULL REFERENCES agent_event_inbox(inbox_seq)
                ON DELETE RESTRICT,
            last_inbox_seq INTEGER NOT NULL REFERENCES agent_event_inbox(inbox_seq)
                ON DELETE RESTRICT,
            UNIQUE(session_id, provider_run_id),
            CHECK(last_inbox_seq >= first_inbox_seq),
            CHECK(status!='running' OR started_at IS NOT NULL),
            CHECK((status='running' AND ended_at IS NULL)
                OR (status!='running' AND ended_at IS NOT NULL))
        )""",
        """CREATE INDEX idx_agent_session_runs_session_started
           ON agent_session_runs(session_id, started_at, run_record_id)""",
        """CREATE TABLE agent_event_projection_receipts (
            consumer_id TEXT NOT NULL
                CHECK(length(trim(consumer_id)) BETWEEN 1 AND 80),
            agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE RESTRICT,
            inbox_seq INTEGER NOT NULL,
            event_id TEXT NOT NULL
                CHECK(length(trim(event_id)) BETWEEN 1 AND 160),
            event_type TEXT NOT NULL
                CHECK(length(trim(event_type)) BETWEEN 1 AND 64),
            outcome TEXT NOT NULL CHECK(outcome IN (
                'applied','ignored','blocked','skipped'
            )),
            reason_code TEXT
                CHECK(reason_code IS NULL OR
                      length(trim(reason_code)) BETWEEN 1 AND 80),
            attempt_count INTEGER NOT NULL DEFAULT 1 CHECK(attempt_count >= 1),
            first_attempt_at REAL NOT NULL,
            last_attempt_at REAL NOT NULL,
            projected_at REAL,
            PRIMARY KEY(consumer_id, agent_id, inbox_seq),
            UNIQUE(consumer_id, agent_id, event_id),
            UNIQUE(consumer_id, agent_id, inbox_seq, event_id),
            FOREIGN KEY(inbox_seq, agent_id)
                REFERENCES agent_event_inbox(inbox_seq, agent_id)
                ON DELETE RESTRICT,
            CHECK((outcome='blocked' AND projected_at IS NULL)
                OR (outcome!='blocked' AND projected_at IS NOT NULL))
        )""",
        """CREATE INDEX idx_agent_event_projection_receipts_outcome
           ON agent_event_projection_receipts(
               consumer_id, agent_id, outcome, inbox_seq
           )""",
        """CREATE TABLE agent_event_projection_resolutions (
            resolution_id TEXT PRIMARY KEY
                CHECK(length(trim(resolution_id)) BETWEEN 1 AND 128),
            consumer_id TEXT NOT NULL
                CHECK(length(trim(consumer_id)) BETWEEN 1 AND 80),
            agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE RESTRICT,
            inbox_seq INTEGER NOT NULL,
            event_id TEXT NOT NULL
                CHECK(length(trim(event_id)) BETWEEN 1 AND 160),
            action TEXT NOT NULL CHECK(action='skip'),
            reason TEXT NOT NULL
                CHECK(length(trim(reason)) BETWEEN 1 AND 500),
            actor TEXT NOT NULL
                CHECK(length(trim(actor)) BETWEEN 1 AND 80),
            resolved_at REAL NOT NULL,
            UNIQUE(consumer_id, agent_id, inbox_seq),
            FOREIGN KEY(consumer_id, agent_id, inbox_seq, event_id)
                REFERENCES agent_event_projection_receipts(
                    consumer_id, agent_id, inbox_seq, event_id
                ) ON DELETE RESTRICT
        )""",
        """CREATE INDEX idx_agent_event_projection_resolutions_agent_time
           ON agent_event_projection_resolutions(agent_id, resolved_at DESC)""",
    ),
)




def apply_session_v7(conn, *, now):
    """Apply exact Foundation v7 after v6, or validate existing v7."""
    if conn.in_transaction:
        raise RuntimeError("session migration requires an idle connection")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise RuntimeError("session migration requires foreign keys")
    if type(now) not in (int, float) or not math.isfinite(now):
        raise ValueError("invalid migration timestamp")
    m = _AGENT_SESSION_PROJECTION_V7
    chain = (_REGISTRY_V1, _TASK_AUDIT_V2, _TASK_HOTPATH_V3,
             _AGENT_EVENT_INBOX_V4, _AGENT_EVENT_GATEWAY_V5,
             _AGENT_EVENT_CONSUMER_V6, m)
    objects = Migration(m.version,m.name,m.statements[1:])
    old_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT version,name,checksum FROM hub_schema_migrations ORDER BY version"
        ).fetchall()
        expected = [(x.version,x.name,_checksum(x)) for x in chain]
        if len(rows) not in (6,7) or [tuple(r) for r in rows] != expected[:len(rows)]:
            raise RuntimeError("unsupported or mismatched Hub migration ledger")
        if [r[0] for r in conn.execute("PRAGMA quick_check")] != ["ok"]:
            raise RuntimeError("database integrity check failed")
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("database foreign key violation")
        # v7 alters the v1 sessions table. Compare its expected post-ALTER
        # SQLite DDL, rather than incorrectly requiring unmodified v1 SQL.
        base = next(s for s in _REGISTRY_V1.statements
                    if s.startswith("CREATE TABLE agent_sessions "))
        remaining = tuple(s for s in _REGISTRY_V1.statements if s != base)
        _verify_objects(conn, Migration(1,_REGISTRY_V1.name,remaining), applied=True)
        for previous in chain[3:6]:
            _verify_objects(conn, previous, applied=True)
        _check_indexes(conn,_V2_INDEXES,required=True)
        _check_indexes(conn,_TASK_HOTPATH_V3.statements,required=True)
        def verify_sessions(upgraded):
            sql = base
            if upgraded:
                addition = m.statements[0].split("ADD COLUMN ",1)[1]
                sql = base.rsplit(")",1)[0] + ", " + addition + ")"
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='agent_sessions'"
            ).fetchone()
            if not row or _normalize(row[0]) != _normalize(sql):
                raise RuntimeError("missing or incompatible agent_sessions schema")
        verify_sessions(len(rows)==7)
        _verify_objects(conn,objects,applied=len(rows)==7)
        applied = []
        if len(rows)==6:
            _preflight_agent_session_projection_v7(conn)
            for sql in m.statements:
                conn.execute(sql)
            conn.execute("INSERT INTO hub_schema_migrations VALUES(?,?,?,?)",
                         (m.version,m.name,_checksum(m),float(now)))
            applied.append(7)
        verify_sessions(True)
        _verify_objects(conn,objects,applied=True)
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("database foreign key violation")
        conn.commit()
        return applied
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.row_factory = old_factory
