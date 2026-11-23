#!/usr/bin/env python3
"""Tests for the isolated SQLite storage primitives."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.storage import connect, write_audit


class StorageTests(unittest.TestCase):
    def test_connect_applies_hub_sqlite_pragmas(self):
        with tempfile.TemporaryDirectory(prefix="pack-storage-test-") as directory:
            path = Path(directory) / "test.db"
            connection = connect(path)
            try:
                self.assertIs(connection.row_factory, sqlite3.Row)
                self.assertEqual(
                    connection.execute("PRAGMA busy_timeout").fetchone()[0],
                    30000,
                )
                self.assertEqual(
                    connection.execute("PRAGMA foreign_keys").fetchone()[0],
                    1,
                )
            finally:
                connection.close()

    def test_init_sets_wal_and_runtime_connections_keep_busy_timeout(self):
        import threading
        import time

        from core.context import HubContext
        from hub import init

        with tempfile.TemporaryDirectory(prefix="pack-storage-init-") as directory:
            root = Path(directory)
            context = HubContext(
                base_dir=str(root),
                db_path=str(root / "pack.db"),
                attachment_dir=str(root / "attachments"),
                lock=threading.Lock(),
                clock=time.time,
            )
            init(context)
            connection = context.connect()
            try:
                self.assertEqual(
                    connection.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                    "wal",
                )
                self.assertEqual(
                    connection.execute("PRAGMA busy_timeout").fetchone()[0],
                    30000,
                )
                self.assertEqual(
                    connection.execute("PRAGMA foreign_keys").fetchone()[0],
                    1,
                )
            finally:
                connection.close()

    def test_write_audit_extracts_only_bounded_task_prefixes(self):
        with tempfile.TemporaryDirectory(prefix="pack-storage-audit-") as directory:
            connection = connect(Path(directory) / "test.db")
            try:
                connection.execute(
                    "CREATE TABLE audit(ts REAL NOT NULL, actor TEXT NOT NULL, "
                    "action TEXT NOT NULL, detail TEXT, task_id INTEGER)"
                )
                for detail in (
                    "task 42: event",
                    "task 9223372036854775808",
                    "task 42x is not a task prefix",
                    "message 42",
                ):
                    write_audit(connection, "member-a", "task_update", detail, timestamp=1)
                rows = connection.execute(
                    "SELECT detail,task_id FROM audit ORDER BY rowid"
                ).fetchall()
                self.assertEqual(
                    [(row["detail"], row["task_id"]) for row in rows],
                    [
                        ("task 42: event", 42),
                        ("task 9223372036854775808", None),
                        ("task 42x is not a task prefix", None),
                        ("message 42", None),
                    ],
                )
                connection.rollback()
            finally:
                connection.close()

    def test_write_audit_uses_supplied_timestamp_and_does_not_commit(self):
        with tempfile.TemporaryDirectory(prefix="pack-storage-test-") as directory:
            connection = connect(Path(directory) / "test.db")
            try:
                connection.execute(
                    "CREATE TABLE audit(ts REAL NOT NULL, actor TEXT NOT NULL, "
                    "action TEXT NOT NULL, detail TEXT, task_id INTEGER)"
                )
                write_audit(
                    connection,
                    "member-a",
                    "task_update",
                    "task=9",
                    timestamp=123.5,
                )
                row = connection.execute("SELECT * FROM audit").fetchone()
                self.assertEqual(tuple(row), (123.5, "member-a", "task_update", "task=9", None))
                self.assertTrue(connection.in_transaction)
                connection.rollback()
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM audit").fetchone()[0], 0)
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
