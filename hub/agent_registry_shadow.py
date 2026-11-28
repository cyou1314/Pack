"""Read-only comparison of legacy Hub members and Agent Registry rows."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from registry_offline_chain import CHAIN as MIGRATIONS, checksum as _checksum, _ALIAS_V8, _verify_schema
from registry_v1_port import _provider_for
from collaboration_migrate import _database_path
from collaboration_identity_inventory import inspect_executor_registry


def _expected_provider(label):
    normalized = (label or "").strip().lower()
    return _ALIAS_V8.get(normalized, _provider_for(label)[0])


def _expected_status(row: sqlite3.Row) -> str:
    if row["removed_at"] is not None:
        return "removed"
    if (row["kind"] or "").strip().lower() == "external":
        return "active"
    login_state = (row["login_state"] or "").strip().lower()
    if login_state in ("pending", "logged_in"):
        return "pending"
    if login_state == "expired":
        return "disconnected"
    return "legacy_unclassified"


def build_shadow_report(connection: sqlite3.Connection) -> dict[str, Any]:
    """Read a coherent snapshot without changing the caller's row factory."""
    old_factory = connection.row_factory
    owns_transaction = not connection.in_transaction
    connection.row_factory = sqlite3.Row
    try:
        if owns_transaction:
            connection.execute("BEGIN")
        return _build_shadow_report(connection)
    finally:
        if owns_transaction and connection.in_transaction:
            connection.rollback()
        connection.row_factory = old_factory


def _build_shadow_report(connection: sqlite3.Connection) -> dict[str, Any]:
    """Compare legacy member projections to Agent Registry without writing.

    Only legacy member IDs and mismatch field names are included in the report;
    tokens, profiles, message content, and memory are never selected.
    """
    connection.row_factory = sqlite3.Row
    tables = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    required_tables = {
        "members", "providers", "agents", "agent_connections",
        "agent_sessions", "hub_schema_migrations",
    }
    missing_tables = sorted(required_tables - tables)
    if missing_tables:
        return {
            "ok": False,
            "ready": False,
            "reason": "registry_schema_missing",
            "missing_tables": missing_tables,
            "mismatch_count": 0,
            "mismatches": [],
        }

    applied_rows = {
        row["version"]: row
        for row in connection.execute(
            "SELECT version,name,checksum FROM hub_schema_migrations"
        )
    }
    known_versions = {item.version for item in MIGRATIONS}
    unsupported_versions = sorted(set(applied_rows) - known_versions)
    if unsupported_versions:
        return {
            "ok": False,
            "ready": False,
            "reason": "unsupported_migration_versions",
            "unsupported_migration_versions": unsupported_versions,
            "mismatch_count": 0,
            "mismatches": [],
        }

    missing_versions = sorted(known_versions - set(applied_rows))
    if missing_versions:
        return {
            "ok": False,
            "ready": False,
            "reason": "registry_migration_missing",
            "missing_migration_versions": missing_versions,
            "migration_checksum_matches": False,
            "mismatch_count": 0,
            "mismatches": [],
        }

    checksum_mismatches = [
        item.version for item in MIGRATIONS
        if (
            applied_rows[item.version]["name"] != item.name
            or applied_rows[item.version]["checksum"] != _checksum(item)
        )
    ]
    checksum_matches = not checksum_mismatches
    if not checksum_matches:
        return {
            "ok": False,
            "ready": False,
            "reason": "migration_checksum_mismatch",
            "migration_checksum_matches": False,
            "checksum_mismatch_versions": checksum_mismatches,
            "mismatch_count": 0,
            "mismatches": [],
        }

    # A zero mapping diff is not sufficient if the database has structural
    # damage or orphaned references in the new registry tables.
    quick_check = [
        row[0] for row in connection.execute("PRAGMA quick_check").fetchall()
    ]
    integrity_ok = quick_check == ["ok"]
    foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
    foreign_key_tables = sorted({str(row[0]) for row in foreign_key_rows})
    if not integrity_ok or foreign_key_rows:
        return {
            "ok": False,
            "ready": False,
            "reason": "database_integrity_check_failed",
            "migration_checksum_matches": checksum_matches,
            "database_integrity_ok": integrity_ok,
            "foreign_key_violation_count": len(foreign_key_rows),
            "foreign_key_violation_tables": foreign_key_tables,
            "mismatch_count": 0,
            "mismatches": [],
        }

    latest_migration_version = max(known_versions)
    try:
        _verify_schema(connection, latest_migration_version)
    except (RuntimeError, sqlite3.Error):
        return {
            "ok": False, "ready": False,
            "reason": "registry_schema_incompatible",
            "migration_checksum_matches": True,
            "mismatch_count": 0, "mismatches": [],
        }

    member_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(members)")
    }
    required_member_columns = {
        "id", "name", "role", "created_at", "removed_at",
        "provider", "display_name", "kind", "login_state",
    }
    missing_columns = sorted(required_member_columns - member_columns)
    if missing_columns:
        return {
            "ok": False,
            "ready": False,
            "reason": "legacy_member_schema_incomplete",
            "missing_member_columns": missing_columns,
            "migration_checksum_matches": checksum_matches,
            "mismatch_count": 0,
            "mismatches": [],
        }

    members = connection.execute(
        """SELECT id,name,created_at,removed_at,provider,display_name,kind,login_state
           FROM members WHERE role='member' ORDER BY id"""
    ).fetchall()
    agents = connection.execute(
        """SELECT agent_id,provider_id,legacy_member_id,display_name,
                  connection_mode,status,legacy_provider_label,created_at,updated_at
           FROM agents WHERE legacy_member_id IS NOT NULL
           ORDER BY legacy_member_id"""
    ).fetchall()
    by_member_id = {row["legacy_member_id"]: row for row in agents}
    mismatches: list[dict[str, Any]] = []
    mapped_ids: set[int] = set()

    for member in members:
        member_id = member["id"]
        agent = by_member_id.get(member_id)
        if agent is None:
            mismatches.append({"legacy_member_id": member_id, "fields": ["missing_agent"]})
            continue
        mapped_ids.add(member_id)
        provider_id = _expected_provider(member["provider"])
        external = (member["kind"] or "").strip().lower() == "external"
        expected = {
            "agent_id": f"legacy-member-{member_id}",
            "provider_id": provider_id,
            "display_name": (member["display_name"] or "").strip() or member["name"],
            "connection_mode": "external_agent" if external else None,
            "status": _expected_status(member),
            "legacy_provider_label": member["provider"],
            "created_at": member["created_at"],
        }
        changed = [
            field for field, value in expected.items()
            if agent[field] != value
        ]
        if changed:
            mismatches.append({"legacy_member_id": member_id, "fields": changed})

    member_ids = {row["id"] for row in members}
    unexpected = [
        row["legacy_member_id"] for row in agents
        if row["legacy_member_id"] not in member_ids
    ]
    for member_id in unexpected:
        mismatches.append({
            "legacy_member_id": member_id,
            "fields": ["unexpected_legacy_mapping"],
        })

    unlinked_agents = connection.execute(
        "SELECT COUNT(*) FROM agents WHERE legacy_member_id IS NULL"
    ).fetchone()[0]
    unmapped_count = len(members) - len(mapped_ids)
    mismatch_count = len(mismatches)
    ready = checksum_matches and integrity_ok and not foreign_key_rows
    # Keep executor diagnostics distinct from strict legacy projection parity.
    # A clean shadow comparison is not proof of account/connection authority.
    inventory = inspect_executor_registry(connection)
    return {
        "authorization_ready": False,
        "production_activation": False,
        "executor_inventory": inventory,
        "ok": ready and mismatch_count == 0,
        "ready": ready,
        "migration_version": latest_migration_version,
        "applied_migration_versions": sorted(applied_rows),
        "migration_checksum_matches": checksum_matches,
        "database_integrity_ok": integrity_ok,
        "foreign_key_violation_count": len(foreign_key_rows),
        "foreign_key_violation_tables": foreign_key_tables,
        "legacy_member_count": len(members),
        "mapped_member_count": len(mapped_ids),
        "unmapped_member_count": unmapped_count,
        "unexpected_legacy_mapping_count": len(unexpected),
        "unlinked_agent_count": unlinked_agents,
        "mismatch_count": mismatch_count,
        "mismatches": mismatches,
    }



def main(argv: list[str] | None = None) -> int:
    """Run the shadow comparison against a database opened strictly read-only."""
    parser = argparse.ArgumentParser(description="Read-only Agent Registry shadow report")
    parser.add_argument(
        "--db",
        type=Path,
        required=True,
        help="Hub SQLite database path (opened read-only)",
    )
    args = parser.parse_args(argv)
    connection = None
    try:
        uri = _database_path(args.db.expanduser()).as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        report = build_shadow_report(connection)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        if report.get("ok"):
            return 0
        return 2 if report.get("ready") else 3
    except Exception as error:
        # Do not echo a database path or any stored data into diagnostic output.
        print(json.dumps({"ok": False, "error": type(error).__name__}, sort_keys=True))
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
