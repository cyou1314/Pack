"""Offline v7 migration preservation and fail-closed regression tests."""
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from event_gateway_test_context import HubContext
from registry_consumer_v6_port import apply_consumer_v6
from registry_session_v7_port import apply_session_v7


class SessionV7MigrationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="pack-v7-port-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        context = HubContext(str(root),str(root/"hub.db"),str(root/"attachments"),
                             threading.RLock(),lambda: 100.0)
        context.initialize(hub)
        self.db = context.connect()
        self.addCleanup(self.db.close)
        apply_consumer_v6(self.db,now=100)
        self.db.execute(
            "INSERT INTO agents(agent_id,provider_id,display_name,status,created_at,updated_at) "
            "VALUES('a','muse','Fixture','active',1,1)")
        self.room = self.db.execute("SELECT id FROM rooms WHERE name='pack-main'").fetchone()[0]
        self.db.commit()

    def session(self, sid, provider_id):
        self.db.execute(
            "INSERT INTO agent_sessions(session_id,agent_id,room_id,provider_session_id,status,created_at,updated_at) "
            "VALUES(?,'a',?,?,'active',1,1)",(sid,self.room,provider_id))
        self.db.commit()

    def ledger(self):
        return [tuple(r) for r in self.db.execute(
            "SELECT version,name,checksum FROM hub_schema_migrations ORDER BY version")]

    def test_upgrade_preserves_sessions_and_old_checksums(self):
        self.session("s","provider-1")
        before=self.ledger()
        self.assertEqual(apply_session_v7(self.db,now=101),[7])
        self.assertEqual(apply_session_v7(self.db,now=102),[])
        self.assertEqual(self.ledger()[:6],before)
        self.assertEqual(tuple(self.db.execute(
            "SELECT provider_session_id,last_inbox_seq FROM agent_sessions WHERE session_id='s'"
        ).fetchone()),("provider-1",0))

    def test_duplicate_legacy_bindings_block_without_writes(self):
        self.session("s1","provider-1")
        self.session("s2","provider-1")
        before=self.ledger()
        with self.assertRaisesRegex(RuntimeError,"legacy Session binding preflight"):
            apply_session_v7(self.db,now=101)
        self.assertEqual(self.ledger(),before)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM agent_sessions").fetchone()[0],2)
        self.assertNotIn("last_inbox_seq",[r[1] for r in self.db.execute("PRAGMA table_info(agent_sessions)")])

    def test_noncanonical_legacy_binding_blocks(self):
        self.session("s"," provider-1 ")
        with self.assertRaisesRegex(RuntimeError,"non-canonical"):
            apply_session_v7(self.db,now=101)
        self.assertEqual(len(self.ledger()),6)

    def test_replay_rejects_missing_projection_index(self):
        apply_session_v7(self.db,now=101)
        self.db.execute("DROP INDEX idx_agent_session_runs_session_started")
        with self.assertRaisesRegex(RuntimeError,"incompatible event object"):
            apply_session_v7(self.db,now=102)

    def test_unversioned_projection_object_blocks(self):
        self.db.execute("CREATE TABLE agent_session_messages(id INTEGER)")
        with self.assertRaisesRegex(RuntimeError,"unversioned event object"):
            apply_session_v7(self.db,now=101)
        self.assertEqual(len(self.ledger()),6)


if __name__ == "__main__":
    unittest.main()
