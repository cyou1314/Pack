"""Unified migration tests on temporary main-based Hub databases."""
import tempfile
import unittest
from pathlib import Path

import hub
from event_gateway_test_context import HubContext
from registry_consumer_v6_port import apply_consumer_v6
from registry_session_v7_port import apply_session_v7
from registry_offline_chain import apply_offline_chain
from registry_v1_port import apply_registry_v1
import threading


class OfflineChainTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix="pack-offline-chain-")
        self.addCleanup(tmp.cleanup)
        root=Path(tmp.name)
        self.context=HubContext(str(root),str(root/"hub.db"),str(root/"attachments"),
                                threading.RLock(),lambda:100.0)
        previous=(hub.BASE,hub.DB_PATH,hub.ATTACH_DIR)
        try:
            hub.BASE,hub.DB_PATH,hub.ATTACH_DIR=(str(root),str(root/"hub.db"),str(root/"attachments"))
            hub.init()
        finally:
            hub.BASE,hub.DB_PATH,hub.ATTACH_DIR=previous
        self.db=self.context.connect()
        self.addCleanup(self.db.close)

    def member(self,label,kind=None):
        mid=self.db.execute(
            "INSERT INTO members(name,role,token_hash,created_at,provider,login_state,kind) "
            "VALUES(?,'member',?,1,?,'logged_in',?)",
            (label,label+"-fixture",label,kind)).lastrowid
        self.db.commit()
        return f"legacy-member-{mid}"

    def apply(self):
        return apply_offline_chain(self.db,now=100)

    def test_full_chain_catalog_and_replay(self):
        ids=[self.member(x) for x in ("gemini-api","volcengine","chatgpt","unknown")]
        self.assertEqual(self.apply(),list(range(1,9)))
        self.assertEqual(self.apply(),[])
        for aid,provider in zip(ids,("gemini","volcengine","openai","legacy")):
            row=self.db.execute(
                "SELECT provider_id,status,connection_mode FROM agents WHERE agent_id=?",(aid,)).fetchone()
            self.assertEqual(tuple(row),(provider,"pending",None))
        for table in ("agent_connections","agent_sessions","agent_event_tokens"):
            self.assertEqual(self.db.execute("SELECT COUNT(*) FROM "+table).fetchone()[0],0)

    def test_resume_v1_preserves_checksum(self):
        aid=self.member("gemini-api")
        apply_registry_v1(self.db,now=90)
        old=tuple(self.db.execute("SELECT * FROM hub_schema_migrations").fetchone())
        self.assertEqual(self.apply(),list(range(2,9)))
        self.assertEqual(tuple(self.db.execute("SELECT * FROM hub_schema_migrations WHERE version=1").fetchone()),old)
        self.assertEqual(self.db.execute("SELECT provider_id FROM agents WHERE agent_id=?",(aid,)).fetchone()[0],"gemini")

    def test_external_conflict_rolls_back_whole_chain(self):
        self.member("gemini-api","external")
        with self.assertRaisesRegex(RuntimeError,"ambiguous legacy identity"):
            self.apply()
        self.assertIsNone(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='agents'").fetchone())
        self.assertIsNone(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='hub_schema_migrations'").fetchone())

    def test_existing_connection_blocks_reclassification(self):
        aid=self.member("volcengine")
        apply_registry_v1(self.db,now=90)
        self.db.execute(
            "INSERT INTO agent_connections(connection_id,agent_id,mode,auth_kind,status,created_at,updated_at) "
            "VALUES('c',?,'official_api','api_key','revoked',1,1)",(aid,))
        self.db.commit()
        with self.assertRaisesRegex(RuntimeError,"dependent records"):
            self.apply()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM hub_schema_migrations").fetchone()[0],1)
        self.assertEqual(self.db.execute("SELECT provider_id FROM agents WHERE agent_id=?",(aid,)).fetchone()[0],"legacy")
        self.assertIsNone(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='agent_event_inbox'").fetchone())

    def test_unknown_ledger_rejected(self):
        self.apply()
        self.db.execute("INSERT INTO hub_schema_migrations VALUES(99,'unknown','bad',1)")
        self.db.commit()
        with self.assertRaisesRegex(RuntimeError,"mismatched"):
            self.apply()

    def test_replay_rejects_missing_provider(self):
        self.apply()
        self.db.execute("DELETE FROM providers WHERE provider_id='volcengine'")
        self.db.commit()
        with self.assertRaisesRegex(RuntimeError,"missing Ark provider"):
            self.apply()


if __name__ == "__main__":
    unittest.main()
