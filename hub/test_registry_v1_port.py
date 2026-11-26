"""Registry v1 port integration against the current Hub initialized schema."""
import os
import sqlite3
import tempfile
import unittest

from test_task_lifecycle_guards import hub
from collaboration_store import initialize
from collaboration_identity_inventory import inspect_executor_registry
from registry_v1_port import apply_registry_v1, _REGISTRY_V1, _checksum


class RegistryPortTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="pack-registry-port-")
        self.addCleanup(tmp.cleanup)
        for attr, value in (("BASE", tmp.name), ("DB_PATH", os.path.join(tmp.name, "pack.db")),
                            ("ATTACH_DIR", os.path.join(tmp.name, "attachments"))):
            old = getattr(hub, attr)
            self.addCleanup(setattr, hub, attr, old)
            setattr(hub, attr, value)
        hub.init()
        self.conn = hub.db()
        self.addCleanup(self.conn.close)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.ids = {}
        for name, provider, login, removed in (
            ("fixture-muse", "muse", "logged_in", None),
            ("fixture-chatgpt", "chatgpt", "pending", None),
            ("fixture-gemini-api", "gemini-api", "logged_in", None),
            ("fixture-ark", "volcengine", "logged_in", None),
            ("fixture-removed", "doubao", "logged_in", 9),
            ("fixture-unknown", None, None, None),
        ):
            self.ids[name] = self.conn.execute(
                "INSERT INTO members(name,role,token_hash,created_at,provider,login_state,removed_at)"
                " VALUES(?,'member',?,1,?,?,?)",
                (name, "fixture-" + name, provider, login, removed)).lastrowid
        self.conn.commit()
        initialize(self.conn)

    def apply(self):
        return apply_registry_v1(self.conn, now=10)

    def test_additive_backfill_preserves_legacy_data(self):
        before = [tuple(row) for row in self.conn.execute("SELECT * FROM members ORDER BY id")]
        self.assertTrue(self.apply())
        self.assertFalse(self.apply())
        self.assertEqual(before, [tuple(row) for row in self.conn.execute("SELECT * FROM members ORDER BY id")])
        expected = {"fixture-muse": ("muse","pending"),
                    "fixture-chatgpt": ("openai","pending"),
                    "fixture-gemini-api": ("legacy","pending"),
                    "fixture-ark": ("legacy","pending"),
                    "fixture-removed": ("doubao","removed"),
                    "fixture-unknown": ("legacy","legacy_unclassified")}
        for name, values in expected.items():
            row = self.conn.execute("SELECT provider_id,status FROM agents WHERE legacy_member_id=?",
                                    (self.ids[name],)).fetchone()
            self.assertEqual(tuple(row), values)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM agent_connections").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM agent_sessions").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT checksum FROM hub_schema_migrations").fetchone()[0],
                         _checksum(_REGISTRY_V1))
        self.assertIsNone(self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='pack_schema_migrations'").fetchone())

    def test_inventory_does_not_authorize_backfilled_member(self):
        self.apply()
        mid = self.ids["fixture-chatgpt"]
        self.conn.execute("INSERT INTO collaboration_executors VALUES(?,1,'chatgpt','native',?,0)",
                          (mid,"11111111-1111-4111-8111-111111111111"))
        self.conn.commit()
        self.conn.execute("BEGIN")
        report = inspect_executor_registry(self.conn)
        self.assertFalse(report["authorization_ready"])
        self.assertIn("missing_connection", report["executors"][0]["issues"])
        self.conn.rollback()

    def test_unknown_version_and_checksum_fail_closed(self):
        self.apply()
        self.conn.execute("UPDATE hub_schema_migrations SET checksum='bad'")
        self.conn.commit()
        with self.assertRaisesRegex(RuntimeError,"mismatched"):
            self.apply()
        self.assertEqual(self.conn.execute("SELECT checksum FROM hub_schema_migrations").fetchone()[0],"bad")
        self.assertFalse(self.conn.in_transaction)

    def test_unversioned_registry_rejected(self):
        self.conn.execute("CREATE TABLE providers(id TEXT)")
        with self.assertRaisesRegex(RuntimeError,"unversioned"):
            self.apply()
        self.assertIsNone(self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='agents'").fetchone())

    def test_requires_idle_connection_and_foreign_keys(self):
        self.conn.execute("BEGIN")
        with self.assertRaisesRegex(RuntimeError,"idle"):
            self.apply()
        self.conn.rollback()
        self.conn.execute("PRAGMA foreign_keys=OFF")
        with self.assertRaisesRegex(RuntimeError,"foreign keys"):
            self.apply()

    def test_rollback_on_backfill_failure(self):
        # Force a genuine mid-migration failure by conflicting with a new index.
        self.conn.execute("CREATE INDEX idx_agents_provider_status ON members(id)")
        self.conn.commit()
        with self.assertRaises(sqlite3.OperationalError):
            self.apply()
        for name in ("providers","agents","agent_connections","agent_sessions","hub_schema_migrations"):
            self.assertIsNone(self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                                (name,)).fetchone())


if __name__ == "__main__":
    unittest.main()
