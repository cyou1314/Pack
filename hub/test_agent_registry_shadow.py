#!/usr/bin/env python3
"""Tests for the read-only Agent Registry shadow report."""
import hashlib
import sqlite3
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from core.migrations import apply_migrations
from core.storage import connect
from services.agent_registry_shadow import build_shadow_report, main


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
            CREATE TABLE audit(
                id INTEGER PRIMARY KEY, ts REAL NOT NULL, actor TEXT NOT NULL,
                action TEXT NOT NULL, detail TEXT
            );
            CREATE TABLE messages(
                id INTEGER PRIMARY KEY, author_id INTEGER NOT NULL,
                kind TEXT NOT NULL, body TEXT NOT NULL, task_id INTEGER
            );
            CREATE TABLE review_log(
                id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE tasks(
                id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL,
                title TEXT NOT NULL, body TEXT NOT NULL,
                created_by INTEGER NOT NULL, assignee INTEGER, status TEXT NOT NULL,
                claim_token TEXT, lease_expires REAL,
                attempts INTEGER NOT NULL DEFAULT 0, result TEXT, envelope TEXT,
                review_note TEXT, fail_class TEXT, next_claim_at REAL,
                sched_at REAL, urgent INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE task_transfers(
                id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL,
                from_member TEXT NOT NULL, to_member TEXT NOT NULL, reason TEXT,
                actor TEXT NOT NULL, created_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', accepted_at REAL, expires_at REAL
            );
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
                self.assertTrue(report["database_integrity_ok"])
                self.assertEqual(report["foreign_key_violation_count"], 0)
                self.assertEqual(report["foreign_key_violation_tables"], [])
                self.assertEqual(report["legacy_member_count"], 1)
                self.assertEqual(report["mapped_member_count"], 1)
                self.assertEqual(report["unmapped_member_count"], 0)
                self.assertEqual(report["mismatch_count"], 0)
                self.assertEqual(report["mismatches"], [])
                self.assertEqual(connection.total_changes, before_changes)
            finally:
                connection.close()


    def test_cli_opens_database_read_only_and_omits_sensitive_member_fields(self):
        with tempfile.TemporaryDirectory(prefix="pack-agent-shadow-cli-") as directory:
            connection = self._database(directory)
            path = Path(directory) / "shadow.db"
            connection.execute(
                "ALTER TABLE members ADD COLUMN token_hash TEXT"
            )
            connection.execute(
                "ALTER TABLE members ADD COLUMN profile TEXT"
            )
            connection.execute(
                "UPDATE members SET token_hash=?, profile=? WHERE id=1",
                ("token-hash-must-not-appear", "private-profile-must-not-appear"),
            )
            connection.commit()
            connection.close()

            before = hashlib.sha256(path.read_bytes()).hexdigest()
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["--db", str(path)])
            after = hashlib.sha256(path.read_bytes()).hexdigest()

            self.assertEqual(exit_code, 0)
            self.assertEqual(before, after)
            self.assertIn('"mismatch_count": 0', output.getvalue())
            self.assertNotIn("token-hash-must-not-appear", output.getvalue())
            self.assertNotIn("private-profile-must-not-appear", output.getvalue())


    def test_report_refuses_readiness_when_foreign_keys_are_violated(self):
        with tempfile.TemporaryDirectory(prefix="pack-agent-shadow-fk-") as directory:
            path = Path(directory) / "shadow.db"
            connection = self._database(directory)
            connection.close()

            # Simulate a database written by a legacy connection that did not
            # enable SQLite FK enforcement. The report itself remains read-only.
            legacy_writer = sqlite3.connect(path)
            try:
                legacy_writer.execute("PRAGMA foreign_keys=OFF")
                legacy_writer.execute(
                    "INSERT INTO agent_connections("
                    "connection_id,agent_id,mode,auth_kind,status,created_at,updated_at"
                    ") VALUES('orphan-connection','missing-agent','official_api',"
                    "'api_key','connected',100,100)"
                )
                legacy_writer.commit()
            finally:
                legacy_writer.close()

            readonly = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                before_changes = readonly.total_changes
                report = build_shadow_report(readonly)
                self.assertFalse(report["ok"])
                self.assertFalse(report["ready"])
                self.assertEqual(report["reason"], "database_integrity_check_failed")
                self.assertTrue(report["database_integrity_ok"])
                self.assertEqual(report["foreign_key_violation_count"], 1)
                self.assertEqual(
                    report["foreign_key_violation_tables"], ["agent_connections"]
                )
                self.assertEqual(readonly.total_changes, before_changes)
            finally:
                readonly.close()

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
                    "VALUES(999,'future_migration','future-checksum',200)"
                )
                connection.commit()
                report = build_shadow_report(connection)
                self.assertFalse(report["ok"])
                self.assertFalse(report["ready"])
                self.assertEqual(report["reason"], "unsupported_migration_versions")
                self.assertEqual(report["unsupported_migration_versions"], [999])
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
