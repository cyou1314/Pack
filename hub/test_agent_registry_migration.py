#!/usr/bin/env python3
"""Regression tests for the additive Agent Registry schema migration."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.migrations import apply_migrations
from core.storage import connect


class AgentRegistryMigrationTests(unittest.TestCase):
    def _database(self, directory: str):
        path = Path(directory) / "registry.db"
        connection = connect(path)
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
                kind TEXT
            );
            CREATE TABLE rooms(
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            INSERT INTO rooms(id,name,created_at) VALUES(1,'room-a',1);
            INSERT INTO members(id,name,role,created_at,provider,display_name,kind)
                VALUES(1,'codex-user','member',10,'Codex',NULL,NULL);
            INSERT INTO members(id,name,role,created_at,provider,display_name,kind)
                VALUES(2,'external-user','member',20,'codex','External Name','external');
            INSERT INTO members(id,name,role,created_at,removed_at,provider,display_name,kind)
                VALUES(3,'removed-user','member',30,25,'','Removed Name',NULL);
            INSERT INTO members(id,name,role,created_at)
                VALUES(4,'dispatcher','dispatcher',40);
            INSERT INTO members(id,name,role,created_at,provider,display_name,kind)
                VALUES(5,'unknown-provider-user','member',50,'unknown-vendor',NULL,NULL);
            """
        )
        connection.commit()
        return connection

    def test_migration_maps_legacy_members_without_changing_them(self):
        with tempfile.TemporaryDirectory(prefix="pack-agent-registry-") as directory:
            connection = self._database(directory)
            try:
                before = connection.execute(
                    "SELECT id,name,role,created_at,removed_at,provider,display_name,kind "
                    "FROM members ORDER BY id"
                ).fetchall()
                apply_migrations(connection, clock=lambda: 100.0)
                after = connection.execute(
                    "SELECT id,name,role,created_at,removed_at,provider,display_name,kind "
                    "FROM members ORDER BY id"
                ).fetchall()
                self.assertEqual([tuple(row) for row in after], [tuple(row) for row in before])

                agents = connection.execute(
                    "SELECT agent_id,provider_id,legacy_member_id,display_name,"
                    "connection_mode,status,legacy_provider_label,created_at,updated_at "
                    "FROM agents ORDER BY legacy_member_id"
                ).fetchall()
                self.assertEqual(len(agents), 4)
                provider_ids = {row[0] for row in connection.execute("SELECT provider_id FROM providers")}
                self.assertNotIn("external", provider_ids)
                self.assertEqual(
                    tuple(agents[0]),
                    ("legacy-member-1", "codex", 1, "codex-user", None,
                     "legacy_unclassified", "Codex", 10.0, 100.0),
                )
                self.assertEqual(
                    tuple(agents[1]),
                    ("legacy-member-2", "codex", 2, "External Name",
                     "external_agent", "active", "codex", 20.0, 100.0),
                )
                self.assertEqual(agents[2]["status"], "removed")
                self.assertEqual(agents[2]["provider_id"], "legacy")
                self.assertEqual(agents[3]["provider_id"], "legacy")
                self.assertEqual(agents[3]["legacy_provider_label"], "unknown-vendor")
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM agent_connections").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM agent_sessions").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT version,name FROM hub_schema_migrations"
                    ).fetchall()[0]["name"],
                    "additive_agent_registry",
                )
            finally:
                connection.close()

    def test_migration_is_idempotent_and_foreign_keys_are_enforced(self):
        with tempfile.TemporaryDirectory(prefix="pack-agent-registry-") as directory:
            connection = self._database(directory)
            try:
                apply_migrations(connection, clock=lambda: 100.0)
                first = connection.execute(
                    "SELECT agent_id,legacy_member_id FROM agents ORDER BY agent_id"
                ).fetchall()
                apply_migrations(connection, clock=lambda: 200.0)
                second = connection.execute(
                    "SELECT agent_id,legacy_member_id FROM agents ORDER BY agent_id"
                ).fetchall()
                self.assertEqual([tuple(row) for row in first], [tuple(row) for row in second])
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM hub_schema_migrations").fetchone()[0],
                    1,
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO agents(agent_id,provider_id,display_name,status,created_at,updated_at) "
                        "VALUES('invalid','missing-provider','Invalid','pending',1,1)"
                    )
                connection.rollback()
            finally:
                connection.close()

    def test_backfill_mapping_changes_are_detected_by_ledger_checksum(self):
        from core import migrations

        with tempfile.TemporaryDirectory(prefix="pack-agent-registry-") as directory:
            connection = self._database(directory)
            original = dict(migrations._PROVIDER_LABELS)
            try:
                apply_migrations(connection, clock=lambda: 100.0)
                migrations._PROVIDER_LABELS["codex"] = ("codex", "Changed Vendor")
                with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                    apply_migrations(connection, clock=lambda: 200.0)
            finally:
                migrations._PROVIDER_LABELS.clear()
                migrations._PROVIDER_LABELS.update(original)
                connection.close()

    def test_failed_migration_rolls_back_schema_and_ledger(self):
        with tempfile.TemporaryDirectory(prefix="pack-agent-registry-") as directory:
            path = Path(directory) / "broken.db"
            connection = connect(path)
            try:
                connection.execute(
                    "CREATE TABLE members(id INTEGER PRIMARY KEY,name TEXT,role TEXT)"
                )
                connection.commit()
                with self.assertRaisesRegex(RuntimeError, "requires members columns"):
                    apply_migrations(connection, clock=lambda: 100.0)
                names = {
                    row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertNotIn("providers", names)
                self.assertNotIn("agents", names)
                self.assertNotIn("hub_schema_migrations", names)
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
