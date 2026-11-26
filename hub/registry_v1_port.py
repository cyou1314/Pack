"""Foundation Registry v1 port for isolated integration databases only.

The v1 DDL, backfill and its checksum inputs are preserved verbatim from
Foundation commit ae45ef3da81f457c3ab54ea7a3bcabae451d98ef. Not called by Hub startup or installer.
The caller must use a disposable, consistent database copy.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import math
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
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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



def apply_registry_v1(connection: sqlite3.Connection, *, now: float) -> bool:
    """Apply v1 alone, atomically. False means the exact version exists.

    Reject other ledger versions and unversioned/partial registry objects.
    Does not activate registry reads, create connections, or apply v2.
    """
    if connection.in_transaction:
        raise RuntimeError("registry migration requires an idle connection")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise RuntimeError("registry migration requires foreign keys")
    if type(now) not in (int, float) or not math.isfinite(now):
        raise ValueError("invalid migration timestamp")
    old_factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN IMMEDIATE")
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        registry = {"providers", "agents", "agent_connections", "agent_sessions"}
        if not {"members", "rooms"} <= tables:
            raise RuntimeError("legacy Hub schema missing")
        if "hub_schema_migrations" in tables:
            rows = connection.execute(
                "SELECT version,name,checksum FROM hub_schema_migrations ORDER BY version"
            ).fetchall()
            if (len(rows) != 1 or tuple(rows[0]) !=
                    (1, _REGISTRY_V1.name, _checksum(_REGISTRY_V1))):
                raise RuntimeError("unsupported or mismatched Hub migration ledger")
            if not registry <= tables:
                raise RuntimeError("partial registry schema")
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("registry foreign key violation")
            connection.commit()
            return False
        if registry & tables:
            raise RuntimeError("unversioned registry schema")
        if connection.execute("PRAGMA quick_check").fetchall()[0][0] != "ok":
            raise RuntimeError("database integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("database foreign key violation")
        connection.execute("""CREATE TABLE hub_schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at REAL NOT NULL
        )""")
        for statement in _REGISTRY_V1.statements:
            connection.execute(statement)
        _apply_registry_v1(connection, float(now))
        connection.execute(
            "INSERT INTO hub_schema_migrations(version,name,checksum,applied_at) VALUES(?,?,?,?)",
            (1, _REGISTRY_V1.name, _checksum(_REGISTRY_V1), float(now)))
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("registry foreign key violation")
        connection.commit()
        return True
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.row_factory = old_factory
