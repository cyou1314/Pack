"""Synthetic combined-schema contract tests; no real Provider or migration."""
import sqlite3
import unittest

from collaboration_store import initialize, write_transaction
from collaboration_connection_binding import (
    SCHEMA, bind_connection, resolve_connection, revoke_connection,
)

ACCOUNT = "11111111-1111-4111-8111-111111111111"


class ConnectionBindingTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript("""
            CREATE TABLE members(id INTEGER PRIMARY KEY,name TEXT,role TEXT,
                kind TEXT,removed_at REAL,provider TEXT,access_mode TEXT);
            INSERT INTO members VALUES(1,'owner','member',NULL,NULL,NULL,NULL),
                (2,'worker','member',NULL,NULL,'chatgpt','full'),
                (3,'other','member',NULL,NULL,'openai','full');
            CREATE TABLE providers(provider_id TEXT PRIMARY KEY,status TEXT);
            CREATE TABLE agents(agent_id TEXT PRIMARY KEY,provider_id TEXT REFERENCES providers,
                legacy_member_id INTEGER UNIQUE REFERENCES members,status TEXT);
            CREATE TABLE agent_connections(connection_id TEXT PRIMARY KEY,
                agent_id TEXT REFERENCES agents,mode TEXT,status TEXT);
            INSERT INTO providers VALUES('openai','active');
            INSERT INTO agents VALUES('a','openai',2,'active');
            INSERT INTO agent_connections VALUES('c','a','native_account','connected');
        """)
        initialize(self.conn)
        with write_transaction(self.conn):
            self.conn.execute(SCHEMA)
            self.conn.execute("INSERT INTO collaboration_executors VALUES(2,1,'chatgpt','native',?,0)",
                              (ACCOUNT,))

    def bind(self, **overrides):
        args = dict(actor_id=1, member_id=2, account_ref=ACCOUNT,
                    agent_id="a", connection_id="c")
        args.update(overrides)
        return bind_connection(self.conn, **args)

    def resolve(self, **overrides):
        args = dict(member_id=2, account_ref=ACCOUNT)
        args.update(overrides)
        return resolve_connection(self.conn, **args)

    def test_bind_and_exact_replay(self):
        with write_transaction(self.conn):
            self.assertTrue(self.bind())
            self.assertFalse(self.bind())
            self.assertEqual(self.resolve(), ("a", "c"))

    def test_wrong_owner_and_account(self):
        with write_transaction(self.conn):
            for args in (dict(actor_id=3), dict(account_ref="22222222-2222-4222-8222-222222222222")):
                with self.assertRaises(ValueError):
                    self.bind(**args)
            self.assertTrue(self.bind())

    def test_wrong_connection_and_mode(self):
        with write_transaction(self.conn):
            with self.assertRaises(ValueError):
                self.bind(connection_id="other")
            self.conn.execute("UPDATE agent_connections SET mode='official_api'")
            with self.assertRaises(ValueError):
                self.bind()

    def test_duplicate_revoked_account(self):
        with write_transaction(self.conn):
            self.conn.execute("INSERT INTO collaboration_executors VALUES(3,1,'openai','api',?,1)",
                              (ACCOUNT,))
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                self.bind()

    def test_connection_replaced(self):
        with write_transaction(self.conn):
            self.bind()
            self.conn.execute("UPDATE agent_connections SET status='revoked'")
            self.conn.execute("INSERT INTO agent_connections VALUES('new','a','native_account','connected')")
            with self.assertRaises(ValueError):
                self.resolve()
            with self.assertRaises(ValueError):
                self.bind(connection_id="new")

    def test_revoke_tombstone(self):
        with write_transaction(self.conn):
            self.bind()
            with self.assertRaises(ValueError):
                revoke_connection(self.conn, actor_id=3, member_id=2)
            self.assertTrue(revoke_connection(self.conn, actor_id=1, member_id=2))
            self.assertFalse(revoke_connection(self.conn, actor_id=1, member_id=2))
            with self.assertRaises(ValueError):
                self.bind()
            with self.assertRaises(ValueError):
                self.resolve()

    def test_executor_revocation_and_owner_removal(self):
        with write_transaction(self.conn):
            self.bind()
            self.conn.execute("UPDATE collaboration_executors SET revoked=1")
            with self.assertRaises(ValueError):
                self.resolve()
            self.conn.execute("UPDATE collaboration_executors SET revoked=0")
            self.conn.execute("UPDATE members SET removed_at=1 WHERE id=1")
            with self.assertRaises(ValueError):
                self.resolve()

    def test_agent_and_provider_states(self):
        with write_transaction(self.conn):
            self.bind()
            self.conn.execute("UPDATE agents SET status='disabled'")
            with self.assertRaises(ValueError):
                self.resolve()
            self.conn.execute("UPDATE agents SET status='active'")
            self.conn.execute("UPDATE providers SET status='deprecated'")
            with self.assertRaises(ValueError):
                self.resolve()

    def test_foreign_keys_and_rollback(self):
        with self.assertRaises(RuntimeError):
            with write_transaction(self.conn):
                self.bind()
                raise RuntimeError("rollback")
        with write_transaction(self.conn):
            self.assertTrue(self.bind())
            with self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute("DELETE FROM agent_connections WHERE connection_id='c'")

    def test_requires_transaction_and_foreign_keys(self):
        with self.assertRaises(RuntimeError):
            self.bind()
        self.conn.execute("PRAGMA foreign_keys=OFF")
        with write_transaction(self.conn):
            with self.assertRaises(RuntimeError):
                self.bind()


if __name__ == "__main__":
    unittest.main()
