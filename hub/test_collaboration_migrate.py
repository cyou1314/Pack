#!/usr/bin/env python3
"""Offline safety tests for the explicit, additive collaboration DB migration."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("collaboration_migrate", HERE / "collaboration_migrate.py")
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


def make_database(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE rooms(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
    CREATE TABLE members(id INTEGER PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL);
    CREATE TABLE runs(task_id INTEGER, member_id INTEGER, fence INTEGER, claim_token TEXT, status TEXT);
    CREATE TABLE audit(id INTEGER PRIMARY KEY, ts REAL, actor TEXT, action TEXT);
    CREATE TABLE tasks(
      id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL, assignee INTEGER,
      created_by INTEGER NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
      status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
      claim_token TEXT, lease_expires REAL
    );
    INSERT INTO rooms VALUES(1,'pack-main');
    INSERT INTO members VALUES(1,'dispatcher','dispatcher');
    INSERT INTO tasks VALUES(7,1,NULL,1,'legacy task','preserve me','open',0,NULL,NULL);
    """)
    conn.close()


class CollaborationMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pack-collab-migration-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "pack.db"
        self.backups = self.root / "backups" / "migrations"
        make_database(self.db)

    def tables(self):
        with sqlite3.connect(self.db) as conn:
            return {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}

    def test_requires_explicit_opt_in_and_dry_run_is_read_only(self):
        with self.assertRaisesRegex(migration.MigrationError, "explicit"):
            migration.migrate(self.db, enabled=False, backup_dir=self.backups)
        result = migration.migrate(self.db, enabled=True, backup_dir=self.backups, dry_run=True)
        self.assertEqual(result["status"], "ready")
        self.assertNotIn("pack_schema_migrations", self.tables())
        self.assertFalse(self.backups.exists())

    def test_success_creates_verified_backup_and_preserves_existing_rows(self):
        result = migration.migrate(self.db, enabled=True, backup_dir=self.backups)
        self.assertEqual(result["status"], "enabled")
        backup = Path(result["backup"]["path"])
        manifest_path = Path(result["backup"]["manifest"])
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(manifest["sha256"], result["backup"]["sha256"])
        with sqlite3.connect(backup) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("SELECT body FROM tasks WHERE id=7").fetchone()[0], "preserve me")
            self.assertNotIn("pack_schema_migrations", {
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")})
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT body FROM tasks WHERE id=7").fetchone()[0], "preserve me")
            self.assertEqual(conn.execute("SELECT version FROM pack_schema_migrations").fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'collaboration_%'"
            ).fetchone()[0], 4)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        again = migration.migrate(self.db, enabled=True, backup_dir=self.backups)
        self.assertEqual(again["status"], "already_enabled")
        self.assertIsNone(again["backup"])

    def test_partial_or_unversioned_schema_fails_closed_before_backup(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE collaboration_runs(id TEXT PRIMARY KEY)")
        with self.assertRaisesRegex(migration.MigrationError, "unversioned"):
            migration.migrate(self.db, enabled=True, backup_dir=self.backups)
        self.assertFalse(self.backups.exists())
        self.assertNotIn("pack_schema_migrations", self.tables())

    def test_failure_rolls_back_every_schema_object_and_keeps_backup(self):
        original = migration.GUARDS
        migration.GUARDS = original + "\nCREATE TABLE migration_failure_probe(id INTEGER);\nINVALID SQL;\n"
        try:
            with self.assertRaisesRegex(migration.MigrationError, "backup retained"):
                migration.migrate(self.db, enabled=True, backup_dir=self.backups)
        finally:
            migration.GUARDS = original
        tables = self.tables()
        self.assertNotIn("collaboration_runs", tables)
        self.assertNotIn("pack_schema_migrations", tables)
        self.assertNotIn("migration_failure_probe", tables)
        self.assertEqual(list(self.backups.glob("*.db")).__len__(), 1)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT body FROM tasks WHERE id=7").fetchone()[0], "preserve me")
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_symlinked_database_and_backup_escape_are_rejected(self):
        link = self.root / "pack-link.db"
        link.symlink_to(self.db)
        with self.assertRaisesRegex(migration.MigrationError, "regular user-owned"):
            migration.migrate(link, enabled=True, backup_dir=self.backups)
        outside = self.root.parent / (self.root.name + "-outside")
        with self.assertRaisesRegex(migration.MigrationError, "inside the Hub data root"):
            migration._backup_directory(self.root, outside)

    def test_marker_or_schema_tampering_is_rejected(self):
        migration.migrate(self.db, enabled=True, backup_dir=self.backups)
        with sqlite3.connect(self.db) as conn:
            conn.execute("DROP TRIGGER collaboration_task_delete_v1")
        with self.assertRaisesRegex(migration.MigrationError, "incomplete or unexpected"):
            migration.migrate(self.db, enabled=True, backup_dir=self.backups)


if __name__ == "__main__":
    unittest.main()
