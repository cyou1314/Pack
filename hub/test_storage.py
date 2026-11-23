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

    def test_write_audit_uses_supplied_timestamp_and_does_not_commit(self):
        with tempfile.TemporaryDirectory(prefix="pack-storage-test-") as directory:
            connection = connect(Path(directory) / "test.db")
            try:
                connection.execute(
                    "CREATE TABLE audit(ts REAL NOT NULL, actor TEXT NOT NULL, "
                    "action TEXT NOT NULL, detail TEXT)"
                )
                write_audit(
                    connection,
                    "member-a",
                    "task_update",
                    "task=9",
                    timestamp=123.5,
                )
                row = connection.execute("SELECT * FROM audit").fetchone()
                self.assertEqual(tuple(row), (123.5, "member-a", "task_update", "task=9"))
                self.assertTrue(connection.in_transaction)
                connection.rollback()
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM audit").fetchone()[0], 0)
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
