"""Versioned, additive SQLite migrations for the Pack Agent Registry.

Migrations are deliberately separate from the legacy Hub initializer. Each
migration is applied under BEGIN IMMEDIATE and recorded with a content
checksum so repeated or concurrent initialization is safe.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Callable


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
    "external": ("external", "External Agent"),
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


def _checksum(migration: Migration) -> str:
    payload = json.dumps(
        {"version": migration.version, "name": migration.name,
         "statements": migration.statements},
        ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _provider_for(label: str | None, kind: str | None) -> tuple[str, str]:
    if (kind or "").strip().lower() == "external":
        return _PROVIDER_LABELS["external"]
    raw = (label or "").strip()
    return _PROVIDER_LABELS.get(raw.casefold(), _PROVIDER_LABELS.get(raw, ("legacy", "Legacy")))


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
            ("external", "External Agent"),
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
        for name in ("removed_at", "provider", "display_name", "kind")
    }
    rows = connection.execute(
        "SELECT id,name,role,created_at,"
        + ",".join(optional[name] for name in ("removed_at", "provider", "display_name", "kind"))
        + " FROM members WHERE role='member' ORDER BY id"
    ).fetchall()

    for row in rows:
        provider_id, _ = _provider_for(row["provider"], row["kind"])
        external = (row["kind"] or "").strip().lower() == "external"
        removed = row["removed_at"] is not None
        status = "removed" if removed else ("active" if external else "legacy_unclassified")
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


MIGRATIONS = (_REGISTRY_V1,)


def apply_migrations(
    connection: sqlite3.Connection,
    *,
    clock: Callable[[], float],
) -> None:
    """Apply pending Agent Registry migrations atomically and idempotently."""
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

            for statement in migration.statements:
                connection.execute(statement)
            if migration.version == 1:
                _apply_registry_v1(connection, float(clock()))
            connection.execute(
                "INSERT INTO hub_schema_migrations(version,name,checksum,applied_at) "
                "VALUES(?,?,?,?)",
                (migration.version, migration.name, checksum, float(clock())),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
