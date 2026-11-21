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

    def test_init_uses_context_database_clock_and_token_directory(self):
        import sqlite3
        import stat

        import hub

        with tempfile.TemporaryDirectory(prefix="pack-context-init-") as directory:
            root = Path(directory)
            context = HubContext(
                base_dir=str(root),
                db_path=str(root / "isolated.db"),
                attachment_dir=str(root / "attachments"),
                lock=threading.Lock(),
                clock=lambda: 1234.5,
            )

            hub.init(context)

            self.assertTrue((root / "isolated.db").is_file())
            token_path = root / ".dispatcher_token"
            self.assertTrue(token_path.is_file())
            self.assertEqual(stat.S_IMODE(token_path.stat().st_mode) & 0o077, 0)
            connection = sqlite3.connect(root / "isolated.db")
            try:
                member = connection.execute(
                    "SELECT created_at FROM members WHERE role='dispatcher'"
                ).fetchone()
                room = connection.execute(
                    "SELECT created_at FROM rooms WHERE name='pack-main'"
                ).fetchone()
                audit = connection.execute(
                    "SELECT ts,actor,action,detail FROM audit WHERE action='init'"
                ).fetchone()
                self.assertEqual(member[0], 1234.5)
                self.assertEqual(room[0], 1234.5)
                self.assertEqual(audit, (1234.5, "system", "init", "dispatcher created"))
                self.assertTrue(token_path.read_text(encoding="utf-8").startswith("pk-disp-"))
            finally:
                connection.close()

    def test_context_is_used_by_auth_gates_inbox_and_server_factory(self):
        import sqlite3
        from unittest.mock import patch

        import hub

        with tempfile.TemporaryDirectory(prefix="pack-context-gates-") as directory:
            root = Path(directory)
            context = HubContext(
                base_dir=str(root),
                db_path=str(root / "isolated.db"),
                attachment_dir=str(root / "attachments"),
                lock=threading.Lock(),
                clock=lambda: 9876.5,
            )
            hub.init(context)
            token = (root / ".dispatcher_token").read_text(encoding="utf-8").strip()

            with patch(
                "fastmcp.server.dependencies.get_http_headers",
                return_value={"authorization": f"Bearer {token}"},
            ):
                dispatcher = hub.authenticate(context)
            self.assertEqual(dispatcher["role"], "dispatcher")

            with self.assertRaisesRegex(ValueError, "not available to external members"):
                hub._ext_check({"name": "external"}, "not_allowlisted_tool", context)
            with self.assertRaisesRegex(ValueError, "readonly access mode"):
                hub._mode_check(
                    {"name": "readonly-member", "access_mode": "readonly"},
                    "task_create",
                    context,
                )
            self.assertEqual(
                hub._inbox_resolve_room(dispatcher, "pack-main", context),
                (1, "pack-main"),
            )

            connection = sqlite3.connect(root / "isolated.db")
            try:
                actions = {
                    row[0]
                    for row in connection.execute(
                        "SELECT action FROM audit WHERE action IN "
                        "('external_tool_denied','access_mode_denied')"
                    )
                }
                self.assertEqual(actions, {"external_tool_denied", "access_mode_denied"})
            finally:
                connection.close()

            registered = hub.build_server(context).list_tools()
            self.assertEqual(len(registered), 75)


if __name__ == "__main__":
    unittest.main(verbosity=2)
