"""Offline SQLite tests for the read-only identity inventory."""
import sqlite3
import unittest

from collaboration_store import initialize
from collaboration_identity_inventory import inspect_executor_registry


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        initialize(self.conn)
        self.conn.executescript("""
            CREATE TABLE members(id INTEGER PRIMARY KEY, role TEXT, removed_at REAL);
            CREATE TABLE providers(provider_id TEXT PRIMARY KEY, status TEXT);
            CREATE TABLE agents(agent_id TEXT PRIMARY KEY, provider_id TEXT,
                                legacy_member_id INTEGER, status TEXT);
            CREATE TABLE agent_connections(connection_id TEXT PRIMARY KEY,
                    agent_id TEXT, mode TEXT, status TEXT);
            INSERT INTO members VALUES(1,'dispatcher',NULL),(2,'member',NULL);
            INSERT INTO providers VALUES('openai','active');
            INSERT INTO agents VALUES('a','openai',2,'active');
            INSERT INTO agent_connections VALUES('c','a','native_account','connected');
            INSERT INTO collaboration_executors VALUES
                (2,1,'chatgpt','native','11111111-1111-4111-8111-111111111111',0);
        """)

    def tearDown(self):
        self.conn.close()

    def inspect(self):
        self.conn.commit()
        self.conn.execute("BEGIN")
        before = self.conn.total_changes
        report = inspect_executor_registry(self.conn)
        self.assertEqual(self.conn.total_changes, before)
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        return report

    def test_structural_match_is_not_authorization(self):
        report = self.inspect()
        self.assertTrue(report["schema_compatible"])
        self.assertFalse(report["authorization_ready"])
        self.assertEqual(report["executors"], [
            {"member_id": 2, "structural_match": True, "issues": [],
             "explicit_account_binding_required": True}])
        self.assertNotIn("11111111", repr(report))

    def test_mode_mismatch(self):
        self.conn.execute("UPDATE agent_connections SET mode='official_api'")
        self.assertIn("connection_mode_mismatch",
                      self.inspect()["executors"][0]["issues"])

    def test_provider_mismatch(self):
        self.conn.execute("UPDATE agents SET provider_id='gemini'")
        self.assertIn("provider_mismatch",
                      self.inspect()["executors"][0]["issues"])

    def test_missing_registry(self):
        self.conn.execute("DROP TABLE agents")
        report = self.inspect()
        self.assertFalse(report["schema_compatible"])
        self.assertIn("agents", report["missing_or_incompatible"])

    def test_duplicate_account_across_modes(self):
        self.conn.execute("INSERT INTO members VALUES(3,'member',NULL)")
        self.conn.execute("INSERT INTO collaboration_executors VALUES "
                          "(3,1,'openai','api','11111111-1111-4111-8111-111111111111',0)")
        self.assertTrue(all("duplicate_account_ref" in row["issues"]
                            for row in self.inspect()["executors"]))

    def test_revoked_executor_ignored(self):
        self.conn.execute("UPDATE collaboration_executors SET revoked=1")
        self.assertEqual(self.inspect()["executors"], [])

    def test_ambiguous_connection(self):
        self.conn.execute("INSERT INTO agent_connections VALUES "
                          "('other','a','native_account','pending')")
        self.assertIn("ambiguous_connection",
                      self.inspect()["executors"][0]["issues"])

    def test_unknown_mode_and_invalid_owner(self):
        self.conn.execute("UPDATE collaboration_executors SET provider='unknown', owner_id=2")
        issues = self.inspect()["executors"][0]["issues"]
        self.assertIn("unsupported_provider_mode", issues)
        self.assertIn("invalid_executor_owner", issues)

    def test_missing_transaction(self):
        with self.assertRaises(RuntimeError):
            inspect_executor_registry(self.conn)


if __name__ == "__main__":
    unittest.main()
