#!/usr/bin/env python3
"""Tests for isolated HubContext construction and dependency ownership."""
import tempfile
import threading
import unittest
from pathlib import Path

from core.context import HubContext


class HubContextTests(unittest.TestCase):
    def test_context_owns_database_path_lock_and_audit_clock(self):
        with tempfile.TemporaryDirectory(prefix="pack-context-test-") as directory:
            root = Path(directory)
            lock = threading.Lock()
            context = HubContext(
                base_dir=str(root),
                db_path=str(root / "isolated.db"),
                attachment_dir=str(root / "attachments"),
                lock=lock,
                clock=lambda: 456.25,
            )

            self.assertIs(context.lock, lock)
            connection = context.connect()
            try:
                self.assertTrue((root / "isolated.db").exists())
                self.assertEqual(
                    connection.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                    "wal",
                )
                connection.execute(
                    "CREATE TABLE audit(ts REAL, actor TEXT, action TEXT, detail TEXT)"
                )
                context.audit(connection, "test-actor", "context_test", "isolated")
                row = connection.execute("SELECT * FROM audit").fetchone()
                self.assertEqual(
                    tuple(row),
                    (456.25, "test-actor", "context_test", "isolated"),
                )
                self.assertTrue(connection.in_transaction)
                connection.rollback()
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM audit").fetchone()[0],
                    0,
                )
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
