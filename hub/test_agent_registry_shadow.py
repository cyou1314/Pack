#!/usr/bin/env python3
"""Tests for the read-only Agent Registry shadow report."""
import tempfile
import unittest
from pathlib import Path

from core.migrations import apply_migrations
from core.storage import connect
from services.agent_registry_shadow import build_shadow_report


class AgentRegistryShadowTests(unittest.TestCase):
    def _database(self, directory: str):
        connection = connect(Path(directory) / "shadow.db")
        connection.executescript(
            """
            CREATE TABLE members(
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at REAL NOT NULL,
                removed_at REAL,
                provider TEXT,
                display_name TEXT,
                kind TEXT,
                login_state TEXT
            );
            CREATE TABLE rooms(id INTEGER PRIMARY KEY,name TEXT,created_at REAL);
            INSERT INTO rooms(id,name,created_at) VALUES(1,'room-a',1);
            INSERT INTO members(
                id,name,role,created_at,provider,display_name,kind,login_state
            ) VALUES(1,'codex-user','member',10,'codex','Codex Display',NULL,'logged_in');
            INSERT INTO members(id,name,role,created_at)
                VALUES(2,'dispatcher','dispatcher',20);
            """
        )
        connection.commit()
        apply_migrations(connection, clock=lambda: 100.0)
        return connection

    def test_clean_registry_matches_without_writing(self):
        with tempfile.TemporaryDirectory(prefix="pack-agent-shadow-") as directory:
            connection = self._database(directory)
            try:
                before_changes = connection.total_changes
                report = build_shadow_report(connection)
                self.assertTrue(report["ok"])
                self.assertTrue(report["ready"])
                self.assertTrue(report["migration_checksum_matches"])
                self.assertEqual(report["legacy_member_count"], 1)
                self.assertEqual(report["mapped_member_count"], 1)
                self.assertEqual(report["unmapped_member_count"], 0)
                self.assertEqual(report["mismatch_count"], 0)
                self.assertEqual(report["mismatches"], [])
                self.assertEqual(connection.total_changes, before_changes)
            finally:
                connection.close()

    def test_report_detects_mapping_drift_and_non_member_mapping(self):
        with tempfile.TemporaryDirectory(prefix="pack-agent-shadow-") as directory:
            connection = self._database(directory)
            try:
                connection.execute(
                    "UPDATE agents SET display_name='Drifted' WHERE legacy_member_id=1"
                )
                connection.execute(
                    """INSERT INTO agents(
                        agent_id,provider_id,legacy_member_id,display_name,status,
                        created_at,updated_at
                    ) VALUES('legacy-member-2','legacy',2,'dispatcher','legacy_unclassified',20,100)"""
                )
                connection.commit()
                report = build_shadow_report(connection)
                self.assertFalse(report["ok"])
                self.assertEqual(report["mismatch_count"], 2)
                fields = {
                    field
                    for item in report["mismatches"]
                    for field in item["fields"]
                }
                self.assertIn("display_name", fields)
                self.assertIn("unexpected_legacy_mapping", fields)
            finally:
                connection.close()

    def test_report_refuses_to_claim_ready_when_checksum_changes(self):
        from core import migrations

        with tempfile.TemporaryDirectory(prefix="pack-agent-shadow-") as directory:
            connection = self._database(directory)
            original = dict(migrations._PROVIDER_LABELS)
            try:
                migrations._PROVIDER_LABELS["codex"] = ("codex", "Changed")
                report = build_shadow_report(connection)
                self.assertFalse(report["ready"])
                self.assertFalse(report["migration_checksum_matches"])
            finally:
                migrations._PROVIDER_LABELS.clear()
                migrations._PROVIDER_LABELS.update(original)
                connection.close()

    def test_unknown_migration_version_is_not_ready(self):
        with tempfile.TemporaryDirectory(prefix="pack-agent-shadow-") as directory:
            connection = self._database(directory)
            try:
                connection.execute(
                    "INSERT INTO hub_schema_migrations(version,name,checksum,applied_at) "
                    "VALUES(2,'future_migration','future-checksum',200)"
                )
                connection.commit()
                report = build_shadow_report(connection)
                self.assertFalse(report["ok"])
                self.assertFalse(report["ready"])
                self.assertEqual(report["reason"], "unsupported_migration_versions")
                self.assertEqual(report["unsupported_migration_versions"], [2])
            finally:
                connection.close()

    def test_missing_registry_schema_returns_safe_not_ready_report(self):
        with tempfile.TemporaryDirectory(prefix="pack-agent-shadow-") as directory:
            connection = connect(Path(directory) / "empty.db")
            try:
                report = build_shadow_report(connection)
                self.assertFalse(report["ready"])
                self.assertEqual(report["reason"], "registry_schema_missing")
                self.assertIn("agents", report["missing_tables"])
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
