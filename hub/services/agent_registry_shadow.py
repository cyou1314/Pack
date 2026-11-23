"""Read-only comparison of legacy Hub members and Agent Registry rows."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from core.migrations import MIGRATIONS, _checksum, _provider_for


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

    migration = connection.execute(
        "SELECT version,name,checksum FROM hub_schema_migrations WHERE version=?",
        (MIGRATIONS[0].version,),
    ).fetchone()
    checksum_matches = bool(
        migration
        and migration["name"] == MIGRATIONS[0].name
        and migration["checksum"] == _checksum(MIGRATIONS[0])
    )
    if not migration:
        return {
            "ok": False,
            "ready": False,
            "reason": "registry_migration_missing",
            "migration_checksum_matches": False,
            "mismatch_count": 0,
            "mismatches": [],
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
        provider_id, _ = _provider_for(member["provider"])
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
    ready = checksum_matches
    return {
        "ok": ready and mismatch_count == 0,
        "ready": ready,
        "migration_version": migration["version"],
        "migration_checksum_matches": checksum_matches,
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
        default=Path(__file__).resolve().parents[1] / "pack.db",
        help="Hub SQLite database path (opened read-only)",
    )
    args = parser.parse_args(argv)
    connection = None
    try:
        uri = args.db.expanduser().resolve().as_uri() + "?mode=ro"
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
