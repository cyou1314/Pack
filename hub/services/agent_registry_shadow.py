"""Read-only comparison of legacy Hub members and Agent Registry rows."""
from __future__ import annotations

import sqlite3
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
