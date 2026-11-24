#!/usr/bin/env python3
"""Tests for the isolated SQLite storage primitives."""
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from core.context import HubContext
from core.storage import connect, write_audit
from hub import init as init_hub


class StorageTests(unittest.TestCase):
    def test_connect_applies_hub_sqlite_pragmas(self):
        with tempfile.TemporaryDirectory(prefix="pack-storage-test-") as directory:
            path = Path(directory) / "test.db"
            connection = connect(path)
            try:
                self.assertIs(connection.row_factory, sqlite3.Row)
                # Opening a request connection must not attempt a persistent
                # journal-mode transition; fresh SQLite databases stay in DELETE.
                self.assertEqual(
                    connection.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                    "delete",
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

    def test_hub_init_enables_wal_and_request_connections_preserve_it(self):
        with tempfile.TemporaryDirectory(prefix="pack-storage-init-test-") as directory:
            root = Path(directory)
            database = root / "hub.db"
            context = HubContext(
                base_dir=str(root),
                db_path=str(database),
                attachment_dir=str(root / "attachments"),
                lock=threading.RLock(),
                clock=time.time,
            )

            initial = connect(database)
            try:
                self.assertEqual(
                    initial.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                    "delete",
                )
            finally:
                initial.close()

            init_hub(context)

            request_connection = context.connect()
            try:
                self.assertEqual(
                    request_connection.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                    "wal",
                )
            finally:
                request_connection.close()

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
                write_audit(
                    connection, "member-b", "task_claim", "task 9 claimed", timestamp=124.5
                )
                rows = connection.execute("SELECT * FROM audit ORDER BY ts").fetchall()
                self.assertEqual(
                    [tuple(row) for row in rows],
                    [
                        (123.5, "member-a", "task_update", "task=9", None),
                        (124.5, "member-b", "task_claim", "task 9 claimed", 9),
                    ],
                )
                self.assertTrue(connection.in_transaction)
                connection.rollback()
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM audit").fetchone()[0], 0)
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
