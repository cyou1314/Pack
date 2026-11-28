"""Per-agent control regression against versioned disposable Hub schema."""
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from event_gateway_test_context import HubContext
from registry_offline_chain import apply_offline_chain
from collaboration_migrate import migrate, inspect_database, MigrationError
from registry_binding_migration import apply_binding_extension, apply_control_extension
from collaboration_connection_binding import bind_connection
from collaboration_store import write_transaction
from agent_execution_control import set_execution_state, require_execution
from agent_event_gateway_boundary import BoundEventGateway


class ExecutionControlTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix="pack-binding-migration-")
        self.addCleanup(tmp.cleanup)
        root=Path(tmp.name)
        self.context=HubContext(str(root),str(root/"hub.db"),str(root/"attachments"),
                                threading.RLock(),lambda:100.0)
        old=(hub.BASE,hub.DB_PATH,hub.ATTACH_DIR)
        try:
            hub.BASE,hub.DB_PATH,hub.ATTACH_DIR=(str(root),str(root/"hub.db"),str(root/"attachments"))
            hub.init()
        finally:
            hub.BASE,hub.DB_PATH,hub.ATTACH_DIR=old
        migrate(self.context.db_path,enabled=True)
        self.db=self.context.connect()
        self.addCleanup(self.db.close)
        apply_offline_chain(self.db,now=100)

        apply_binding_extension(self.db,now=101)
        self.assertTrue(apply_control_extension(self.db,now=102))
        self.assertFalse(apply_control_extension(self.db,now=103))
        self.accounts=["11111111-1111-4111-8111-111111111111",
                       "22222222-2222-4222-8222-222222222222"]
        self.members=[]
        with write_transaction(self.db):
            self.owner=self.db.execute(
                "INSERT INTO members(name,role,token_hash,created_at) "
                "VALUES('owner','member','owner-fixture',1)").lastrowid
            for i,account in enumerate(self.accounts):
                mid=self.db.execute(
                    "INSERT INTO members(name,role,token_hash,created_at,provider,access_mode) "
                    "VALUES(?,'member',?,1,'chatgpt','full')",(f"worker-{i}",f"fixture-{i}")).lastrowid
                self.members.append(mid)
                self.db.execute("INSERT INTO collaboration_executors VALUES(?,?,'chatgpt','native',?,0)",
                                (mid,self.owner,account))
                self.db.execute(
                    "INSERT INTO agents(agent_id,provider_id,legacy_member_id,display_name,status,created_at,updated_at) "
                    "VALUES(?,'openai',?,?,'active',1,1)",(f"a{i}",mid,f"worker-{i}"))
                self.db.execute(
                    "INSERT INTO agent_connections(connection_id,agent_id,mode,auth_kind,status,created_at,updated_at) "
                    "VALUES(?,?,'native_account','none','connected',1,1)",(f"c{i}",f"a{i}"))
                bind_connection(self.db,actor_id=self.owner,member_id=mid,account_ref=account,
                                agent_id=f"a{i}",connection_id=f"c{i}")
        self.dispatcher=dict(self.db.execute("SELECT id,name FROM members WHERE role='dispatcher'").fetchone())

    def state(self,state,i=0,actor=None):
        return set_execution_state(self.db,actor_id=self.owner if actor is None else actor,
                                   member_id=self.members[i],state=state,now=104)

    def check(self,i=0,**kwargs):
        return require_execution(self.db,member_id=self.members[i],account_ref=self.accounts[i],**kwargs)

    def test_default_off_and_independent_enable(self):
        with write_transaction(self.db):
            with self.assertRaises(PermissionError):
                self.check(feature_enabled=True)
            self.assertEqual(self.state("enabled")["generation"],1)
            with self.assertRaises(PermissionError):
                self.check()
            self.assertEqual(self.check(feature_enabled=True)["agent_id"],"a0")
            with self.assertRaises(PermissionError):
                self.check(1,feature_enabled=True)

    def test_pause_resume_fences_stale_generation(self):
        with write_transaction(self.db):
            self.state("enabled")
            self.state("enabled",1)
            self.assertFalse(self.state("enabled")["changed"])
            self.assertEqual(self.state("paused")["generation"],2)
            with self.assertRaises(PermissionError):
                self.check(feature_enabled=True)
            self.assertEqual(self.check(1,feature_enabled=True)["generation"],1)
            self.assertEqual(self.state("enabled")["generation"],3)
            with self.assertRaisesRegex(PermissionError,"stale_generation"):
                self.check(feature_enabled=True,expected_generation=1)
            self.assertEqual(self.check(feature_enabled=True,expected_generation=3)["generation"],3)

    def test_disconnected_agent_cannot_enable_or_dispatch(self):
        with write_transaction(self.db):
            self.state("enabled")
            self.db.execute("UPDATE agent_connections SET status='disconnected' WHERE connection_id='c0'")
            with self.assertRaises(PermissionError):
                self.check(feature_enabled=True)
            self.state("paused")
            with self.assertRaises(ValueError):
                self.state("enabled")

    def test_owner_required_and_rollback(self):
        with self.assertRaises(RuntimeError):
            with write_transaction(self.db):
                with self.assertRaises(ValueError):
                    self.state("enabled",actor=self.members[1])
                self.state("enabled")
                raise RuntimeError("rollback")
        with write_transaction(self.db):
            self.assertEqual(self.state("enabled")["generation"],1)
            self.assertEqual(self.db.execute(
                "SELECT COUNT(*) FROM audit WHERE action='agent_execution_control'").fetchone()[0],1)

    def test_pause_keeps_event_delivery_but_revocation_denies_it(self):
        gateway=BoundEventGateway(self.context)
        issued=gateway.redeem(gateway.create_pair(self.dispatcher,"a0")["pair_code"])
        with write_transaction(self.db):
            self.state("enabled")
            self.state("paused")
        event={"eventId":"e1","eventType":"run.progress","sequence":1,
               "occurredAt":"2026-01-01T00:00:00Z","payload":{"progress":50}}
        self.assertTrue(gateway.ingest(issued["token"],event)["accepted"])
        with write_transaction(self.db):
            self.state("revoked")
            self.assertFalse(self.state("revoked")["changed"])
            with self.assertRaises(PermissionError):
                self.state("enabled")
        self.assertIsNone(gateway.authenticate(issued["token"]))
        with self.assertRaises(PermissionError):
            gateway.ingest(issued["token"],event)

    def test_versioned_control_compatible_and_tamper_fails(self):
        self.assertEqual(inspect_database(self.context.db_path)["status"],"already_enabled")
        self.db.execute("UPDATE pack_registry_binding_migrations SET checksum='bad' WHERE version=2")
        self.db.commit()
        with self.assertRaises(MigrationError):
            inspect_database(self.context.db_path)


if __name__=="__main__":
    unittest.main()
