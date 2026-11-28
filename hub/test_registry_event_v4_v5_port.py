"""Exercise canonical v4/v5 constraints against the actual Hub schema."""
import os
import sqlite3
import tempfile
import unittest

from test_task_lifecycle_guards import hub
from registry_v1_port import apply_registry_v1
from registry_v2_v3_port import apply_registry_v2_v3, _checksum
from registry_event_v4_v5_port import (
    apply_event_v4_v5, _AGENT_EVENT_INBOX_V4, _AGENT_EVENT_GATEWAY_V5,
)


class EventSchemaTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="pack-event-schema-")
        self.addCleanup(tmp.cleanup)
        for attr, value in (("BASE", tmp.name), ("DB_PATH", os.path.join(tmp.name, "pack.db")),
                            ("ATTACH_DIR", os.path.join(tmp.name, "attachments"))):
            self.addCleanup(setattr, hub, attr, getattr(hub, attr))
            setattr(hub, attr, value)
        hub.init()
        self.conn = hub.db()
        self.addCleanup(self.conn.close)
        self.conn.execute("PRAGMA foreign_keys=ON")
        mid = self.conn.execute(
            "INSERT INTO members(name,role,token_hash,created_at,provider) "
            "VALUES('event-fixture','member','fixture',1,'muse')").lastrowid
        self.agent = f"legacy-member-{mid}"
        self.conn.commit()
        apply_registry_v1(self.conn, now=10)
        apply_registry_v2_v3(self.conn, now=20)

    def apply(self):
        return apply_event_v4_v5(self.conn, now=30)

    def versions(self):
        return [r[0] for r in self.conn.execute(
            "SELECT version FROM hub_schema_migrations ORDER BY version")]

    def event(self, event_id="e1", agent=None, seq=0):
        self.conn.execute(
            "INSERT INTO agent_event_inbox(agent_id,event_id,provider_id,event_type,"
            "event_sequence,occurred_at,event_json,received_at) VALUES(?,?,'muse',"
            "'fixture',?,'2026-01-01T00:00:00Z','{}',30)",
            (self.agent if agent is None else agent,event_id,seq))

    def pair(self, digest):
        self.conn.execute(
            "INSERT INTO agent_event_pair_codes(agent_id,code_hash,created_by,"
            "created_at,expires_at) VALUES(?,?,'fixture',30,60)", (self.agent,digest))

    def token(self, digest, scope):
        self.conn.execute(
            "INSERT INTO agent_event_tokens(agent_id,token_hash,scope,created_by,"
            "created_at,expires_at) VALUES(?,?,?,'fixture',30,60)",
            (self.agent,digest,scope))

    def test_apply_replay_and_checksums(self):
        self.assertEqual(self.apply(), [4,5])
        self.assertEqual(self.versions(), [1,2,3,4,5])
        self.assertEqual(self.apply(), [])
        for m in (_AGENT_EVENT_INBOX_V4,_AGENT_EVENT_GATEWAY_V5):
            self.assertEqual(self.conn.execute(
                "SELECT checksum FROM hub_schema_migrations WHERE version=?", (m.version,)
            ).fetchone()[0], _checksum(m))
        for table in ("agent_event_inbox","agent_event_tokens","agent_event_pair_codes"):
            self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0],0)

    def test_resume_valid_v4(self):
        m = _AGENT_EVENT_INBOX_V4
        for sql in m.statements:
            self.conn.execute(sql)
        self.conn.execute("INSERT INTO hub_schema_migrations VALUES(?,?,?,?)",
                          (m.version,m.name,_checksum(m),30))
        self.event()
        self.conn.commit()
        self.assertEqual(self.apply(), [5])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM agent_event_inbox").fetchone()[0],1)

    def test_inbox_constraints(self):
        self.apply()
        self.event()
        for kwargs in ({}, {"event_id":"e2","agent":"unknown"}, {"event_id":"e3","seq":-1}):
            with self.assertRaises(sqlite3.IntegrityError):
                self.event(**kwargs)

    def test_pair_code_uniqueness_and_revocation(self):
        self.apply()
        self.pair("hash1")
        with self.assertRaises(sqlite3.IntegrityError):
            self.pair("hash2")
        self.conn.execute("UPDATE agent_event_pair_codes SET revoked_at=31")
        self.pair("hash2")
        with self.assertRaises(sqlite3.IntegrityError):
            self.pair("hash1")

    def test_token_scope_and_uniqueness(self):
        self.apply()
        self.token("hash1","events:write")
        for digest,scope in (("hash1","events:write"),("hash2","admin")):
            with self.assertRaises(sqlite3.IntegrityError):
                self.token(digest,scope)

    def test_unversioned_v5_object_refuses_entire_migration(self):
        self.conn.execute("CREATE TABLE agent_event_tokens(id INTEGER)")
        with self.assertRaisesRegex(RuntimeError,"unversioned"):
            self.apply()
        self.assertEqual(self.versions(),[1,2,3])
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='agent_event_inbox'").fetchone())

    def test_replay_rejects_changed_index(self):
        self.apply()
        self.conn.execute("DROP INDEX idx_agent_event_inbox_pending")
        self.conn.execute("CREATE INDEX idx_agent_event_inbox_pending ON agent_event_inbox(event_id)")
        with self.assertRaisesRegex(RuntimeError,"incompatible event object"):
            self.apply()

    def test_wrong_ledger(self):
        self.conn.execute("UPDATE hub_schema_migrations SET checksum='wrong' WHERE version=2")
        self.conn.commit()
        with self.assertRaisesRegex(RuntimeError,"mismatched"):
            self.apply()
        self.assertEqual(self.versions(),[1,2,3])

    def test_requires_foreign_keys_and_idle_connection(self):
        self.conn.execute("BEGIN")
        with self.assertRaisesRegex(RuntimeError,"idle"):
            self.apply()
        self.conn.rollback()
        self.conn.execute("PRAGMA foreign_keys=OFF")
        with self.assertRaisesRegex(RuntimeError,"foreign keys"):
            self.apply()


if __name__ == "__main__":
    unittest.main()
