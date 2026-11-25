"""Versioned, additive SQLite migrations for Pack Hub data.

The ledger covers the Agent Registry, task audit, Agent Event Inbox / Gateway / Consumer,
and Agent Session projection schema. Each migration is applied under BEGIN IMMEDIATE
and recorded with a content checksum so repeated or concurrent initialization is safe.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import sqlite3
from dataclasses import dataclass
from typing import Callable

from .task_refs import (
    APPROVAL_TASK_RE, AUDIT_TASK_RE, SQLITE_INT_MAX,
    audit_task_id, bounded_task_id,
)


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


_PROVIDER_LABELS = {
    "muse": ("muse", "Muse"),
    "codex": ("codex", "Codex"),
    "gemini": ("gemini", "Gemini"),
    "chatgpt": ("openai", "OpenAI"),
    "openai": ("openai", "OpenAI"),
    "doubao": ("doubao", "豆包"),
    "豆包": ("doubao", "豆包"),
    "grok": ("grok", "Grok"),
    "hark": ("hark", "Hark"),
}

_REGISTRY_V1 = Migration(
    version=1,
    name="additive_agent_registry",
    statements=(
        """CREATE TABLE providers (
            provider_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('active','deprecated')),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )""",
        """CREATE TABLE agents (
            agent_id TEXT PRIMARY KEY,
            provider_id TEXT NOT NULL REFERENCES providers(provider_id) ON DELETE RESTRICT,
            legacy_member_id INTEGER UNIQUE REFERENCES members(id) ON DELETE RESTRICT,
            display_name TEXT NOT NULL,
            connection_mode TEXT,
            status TEXT NOT NULL CHECK(status IN (
                'pending','active','disconnected','disabled','removed','legacy_unclassified'
            )),
            legacy_provider_label TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )""",
        """CREATE TABLE agent_connections (
            connection_id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE RESTRICT,
            mode TEXT NOT NULL CHECK(mode IN (
                'native_account','local_runtime','official_api','mcp_connector',
                'external_agent','browser_bridge'
            )),
            auth_kind TEXT NOT NULL CHECK(auth_kind IN (
                'none','oauth','api_key','cli_session','mcp_connector','local_process'
            )),
            credential_ref TEXT,
            status TEXT NOT NULL CHECK(status IN (
                'pending','connected','disconnected','revoked','error'
            )),
            safe_metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            revoked_at REAL
        )""",
        """CREATE UNIQUE INDEX idx_agent_connections_one_active
            ON agent_connections(agent_id)
            WHERE status IN ('pending','connected')""",
        """CREATE TABLE agent_sessions (
            session_id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE RESTRICT,
            room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE RESTRICT,
            provider_session_id TEXT,
            status TEXT NOT NULL CHECK(status IN (
                'creating','active','interrupted','completed','failed','disconnected'
            )),
            last_event_sequence INTEGER NOT NULL DEFAULT 0 CHECK(last_event_sequence >= 0),
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            last_used_at REAL
        )""",
        "CREATE INDEX idx_agents_provider_status ON agents(provider_id, status)",
        "CREATE INDEX idx_agent_sessions_agent_room ON agent_sessions(agent_id, room_id)",
    ),
)


def _normalized_function_source(function: Callable[..., object]) -> str:
    """Return a Python-version-independent representation of a function body."""
    lines = inspect.getsource(function).splitlines()
    return "\n".join(
        line.rstrip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    )


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


def _provider_for(label: str | None) -> tuple[str, str]:
    """Resolve vendor identity independently from the connection mechanism."""
    raw = (label or "").strip()
    return _PROVIDER_LABELS.get(
        raw.casefold(), _PROVIDER_LABELS.get(raw, ("legacy", "Legacy"))
    )


def _apply_registry_v1(connection: sqlite3.Connection, now: float) -> None:
    known = {
        provider_id: display_name
        for provider_id, display_name in (
            ("legacy", "Legacy / Unclassified"),
            ("muse", "Muse"),
            ("codex", "Codex"),
            ("gemini", "Gemini"),
            ("openai", "OpenAI"),
            ("doubao", "豆包"),
            ("grok", "Grok"),
            ("hark", "Hark"),
        )
    }
    connection.executemany(
        "INSERT INTO providers(provider_id,display_name,status,created_at,updated_at) "
        "VALUES(?,?,'active',?,?)",
        [(provider_id, display_name, now, now) for provider_id, display_name in known.items()],
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(members)")}
    required = {"id", "name", "role", "created_at"}
    missing = sorted(required - columns)
    if missing:
        raise RuntimeError(f"Agent Registry migration requires members columns: {', '.join(missing)}")

    optional = {
        name: (name if name in columns else f"NULL AS {name}")
        for name in ("removed_at", "provider", "display_name", "kind", "login_state")
    }
    rows = connection.execute(
        "SELECT id,name,role,created_at,"
        + ",".join(optional[name] for name in ("removed_at", "provider", "display_name", "kind", "login_state"))
        + " FROM members WHERE role='member' ORDER BY id"
    ).fetchall()

    for row in rows:
        provider_id, _ = _provider_for(row["provider"])
        external = (row["kind"] or "").strip().lower() == "external"
        removed = row["removed_at"] is not None
        login_state = (row["login_state"] or "").strip().lower()
        if removed:
            status = "removed"
        elif external:
            status = "active"
        elif login_state in ("pending", "logged_in"):
            # logged_in means the account login succeeded, but the Agent
            # Runtime/adapter is not yet connected.
            status = "pending"
        elif login_state == "expired":
            status = "disconnected"
        else:
            status = "legacy_unclassified"
        connection.execute(
            """INSERT INTO agents(
                agent_id,provider_id,legacy_member_id,display_name,connection_mode,
                status,legacy_provider_label,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                f"legacy-member-{row['id']}", provider_id, row["id"],
                (row["display_name"] or "").strip() or row["name"],
                "external_agent" if external else None,
                status, row["provider"], row["created_at"], now,
            ),
        )


def _apply_task_audit_v2(connection: sqlite3.Connection) -> None:
    """Add structured audit ownership, backfill legacy prefixes and index lookups."""
    required_tables = {"audit", "messages", "review_log"}
    existing_tables = {
        row["name"] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing = sorted(required_tables - existing_tables)
    if missing:
        raise RuntimeError(
            "structured task audit migration requires tables: " + ", ".join(missing)
        )

    audit_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(audit)")
    }
    if "task_id" not in audit_columns:
        connection.execute("ALTER TABLE audit ADD COLUMN task_id INTEGER")

    # Preserve existing non-NULL associations and infer only canonical legacy
    # prefixes. Task-like text elsewhere is deliberately left unassociated.
    for row in connection.execute(
        "SELECT id,detail FROM audit WHERE task_id IS NULL ORDER BY id"
    ):
        task_id = audit_task_id(row["detail"])
        if task_id is not None:
            connection.execute(
                "UPDATE audit SET task_id=? WHERE id=? AND task_id IS NULL",
                (task_id, row["id"]),
            )

    # Backfill historical approval requests so normal reads use the partial index.
    for row in connection.execute(
        "SELECT id,body FROM messages "
        "WHERE kind='approval_request' AND task_id IS NULL ORDER BY id"
    ):
        task_id = bounded_task_id(APPROVAL_TASK_RE.search(row["body"] or ""))
        if task_id is not None:
            connection.execute(
                "UPDATE messages SET task_id=? WHERE id=? AND task_id IS NULL",
                (task_id, row["id"]),
            )

    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_audit_task ON audit(task_id,id)",
        """CREATE INDEX IF NOT EXISTS idx_messages_approval_task
           ON messages(task_id,id DESC) WHERE kind='approval_request'""",
        """CREATE INDEX IF NOT EXISTS idx_messages_dispatch_decisions
           ON messages(author_id,id) WHERE kind IN ('chat','decision')""",
        "CREATE INDEX IF NOT EXISTS idx_review_log_task_time "
        "ON review_log(task_id,created_at)",
    ):
        connection.execute(statement)


_TASK_AUDIT_V2 = Migration(
    version=2,
    name="structured_task_audit_and_approval_indexes",
    statements=(),
)


_TASK_HOTPATH_V3 = Migration(
    version=3,
    name="task_queue_lease_and_transfer_hotpath_indexes",
    statements=(
        "CREATE INDEX IF NOT EXISTS idx_tasks_urgent_id "
        "ON tasks(urgent DESC, id ASC)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_assignee_urgent_id "
        "ON tasks(assignee, urgent DESC, id ASC)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_room_urgent_id "
        "ON tasks(room_id, urgent DESC, id ASC)",
        """CREATE INDEX IF NOT EXISTS idx_tasks_active_queue
           ON tasks(urgent DESC, id ASC, assignee)
           WHERE status IN ('open','claimed','working')""",
        """CREATE INDEX IF NOT EXISTS idx_tasks_lease_expiry
           ON tasks(lease_expires)
           WHERE status IN ('claimed','working') AND lease_expires IS NOT NULL""",
        """CREATE INDEX IF NOT EXISTS idx_tasks_unclaimed_due
           ON tasks(assignee, next_claim_at)
           WHERE status='open' AND assignee IS NOT NULL""",
        """CREATE INDEX IF NOT EXISTS idx_task_transfers_pending_expiry
           ON task_transfers(expires_at, id)
           WHERE status='pending' AND expires_at IS NOT NULL""",
    ),
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
            CHECK(last_attempt_at >= first_attempt_at),
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


MIGRATIONS = (
    _REGISTRY_V1,
    _TASK_AUDIT_V2,
    _TASK_HOTPATH_V3,
    _AGENT_EVENT_INBOX_V4,
    _AGENT_EVENT_GATEWAY_V5,
    _AGENT_EVENT_CONSUMER_V6,
    _AGENT_SESSION_PROJECTION_V7,
)


def apply_migrations(
    connection: sqlite3.Connection,
    *,
    clock: Callable[[], float],
) -> None:
    """Apply pending Hub migrations atomically and idempotently."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS hub_schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at REAL NOT NULL
            )"""
        )
        applied = {
            row["version"]: row
            for row in connection.execute(
                "SELECT version,name,checksum,applied_at FROM hub_schema_migrations"
            )
        }
        known_versions = {migration.version for migration in MIGRATIONS}
        unknown = sorted(set(applied) - known_versions)
        if unknown:
            raise RuntimeError(f"database has unknown Hub migration versions: {unknown}")

        for migration in MIGRATIONS:
            checksum = _checksum(migration)
            previous = applied.get(migration.version)
            if previous is not None:
                if previous["name"] != migration.name or previous["checksum"] != checksum:
                    raise RuntimeError(
                        f"Hub migration checksum mismatch at version {migration.version}"
                    )
                continue

            if migration.version == 7:
                _preflight_agent_session_projection_v7(connection)
            for statement in migration.statements:
                connection.execute(statement)
            if migration.version == 1:
                _apply_registry_v1(connection, float(clock()))
            elif migration.version == 2:
                _apply_task_audit_v2(connection)
            connection.execute(
                "INSERT INTO hub_schema_migrations(version,name,checksum,applied_at) "
                "VALUES(?,?,?,?)",
                (migration.version, migration.name, checksum, float(clock())),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
