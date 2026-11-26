"""Bound Gateway authorization tests on current Hub plus v1-v5."""
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from event_gateway_test_context import HubContext
from collaboration_store import initialize, write_transaction
from collaboration_connection_binding import SCHEMA, bind_connection, revoke_connection
from agent_event_gateway_boundary import BoundEventGateway

ACCOUNT = "11111111-1111-4111-8111-111111111111"


class GatewayBoundaryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="pack-gateway-boundary-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.context = HubContext(str(root), str(root/"hub.db"), str(root/"attachments"),
                                  threading.RLock(), lambda: 100.0)
        self.context.initialize(hub)
        self.gateway = BoundEventGateway(self.context)
        conn = self.context.connect()
        try:
            initialize(conn)
            with write_transaction(conn):
                self.dispatcher = dict(conn.execute(
                    "SELECT id,name FROM members WHERE role='dispatcher'").fetchone())
                self.owner = conn.execute(
                    "INSERT INTO members(name,role,token_hash,created_at) "
                    "VALUES('owner','member','owner-fixture',1)").lastrowid
                self.member = conn.execute(
                    "INSERT INTO members(name,role,token_hash,created_at,provider,access_mode) "
                    "VALUES('worker','member','worker-fixture',1,'chatgpt','full')").lastrowid
                conn.execute(
                    "INSERT INTO collaboration_executors VALUES(?,?,'chatgpt','native',?,0)",
                    (self.member,self.owner,ACCOUNT))
                conn.execute(
                    "INSERT INTO agents(agent_id,provider_id,legacy_member_id,display_name,status,created_at,updated_at) "
                    "VALUES('a','openai',?,'Fixture','active',1,1)",(self.member,))
                conn.execute(
                    "INSERT INTO agent_connections(connection_id,agent_id,mode,auth_kind,status,created_at,updated_at) "
                    "VALUES('c','a','native_account','none','connected',1,1)")
                conn.execute(SCHEMA)
                bind_connection(conn, actor_id=self.owner, member_id=self.member,
                                account_ref=ACCOUNT, agent_id="a", connection_id="c")
        finally:
            conn.close()

    def change(self, sql, args=()):
        conn = self.context.connect()
        try:
            conn.execute(sql,args)
            conn.commit()
        finally:
            conn.close()

    def count(self, table):
        conn = self.context.connect()
        try:
            return conn.execute("SELECT COUNT(*) FROM "+table).fetchone()[0]
        finally:
            conn.close()

    def issue(self):
        return self.gateway.redeem(self.gateway.create_pair(self.dispatcher,"a")["pair_code"])

    def event(self):
        return {"eventId":"event-1","eventType":"run.progress","sequence":1,
                "occurredAt":"2026-01-01T00:00:00Z","payload":{"progress":50}}

    def test_dispatcher_and_bound_agent(self):
        token = self.issue()["token"]
        self.assertEqual(self.gateway.authenticate(token)["agent_id"],"a")
        self.assertTrue(self.gateway.ingest(token,self.event())["accepted"])
        self.assertTrue(self.gateway.ingest(token,self.event())["duplicate"])
        self.assertEqual(self.count("agent_event_inbox"),1)

    def test_forged_role_and_stale_dispatcher(self):
        for actor in ({"id":self.owner,"name":"owner","role":"dispatcher"},
                      {"id":self.dispatcher["id"],"name":"wrong"}):
            with self.assertRaises(PermissionError):
                self.gateway.create_pair(actor,"a")
        for mode in ("readonly","workspace","invalid"):
            self.change("UPDATE members SET access_mode=? WHERE id=?",(mode,self.dispatcher["id"]))
            with self.assertRaises(PermissionError):
                self.gateway.create_pair(self.dispatcher,"a")
        self.assertEqual(self.count("agent_event_pair_codes"),0)

    def test_unbound_agent_denied(self):
        self.change("INSERT INTO agents(agent_id,provider_id,display_name,status,created_at,updated_at) "
                    "VALUES('unbound','openai','Unbound','active',1,1)")
        with self.assertRaises(PermissionError):
            self.gateway.create_pair(self.dispatcher,"unbound")

    def test_binding_revoked_after_pair_before_redeem(self):
        pair = self.gateway.create_pair(self.dispatcher,"a")
        conn = self.context.connect()
        try:
            with write_transaction(conn):
                revoke_connection(conn,actor_id=self.owner,member_id=self.member)
        finally:
            conn.close()
        with self.assertRaisesRegex(ValueError,"invalid_agent_pair_code"):
            self.gateway.redeem(pair["pair_code"])
        self.assertEqual(self.count("agent_event_tokens"),0)

    def test_executor_revocation_blocks_existing_token_and_receipt(self):
        issued = self.issue()
        self.gateway.ingest(issued["token"],self.event())
        self.change("UPDATE collaboration_executors SET revoked=1")
        self.assertIsNone(self.gateway.authenticate(issued["token"]))
        with self.assertRaises(PermissionError):
            self.gateway.capabilities(issued["token"])
        with self.assertRaises(PermissionError):
            self.gateway.ingest(issued["token"],self.event())
        self.assertEqual(self.count("agent_event_inbox"),1)
        self.assertEqual(self.gateway.revoke(self.dispatcher,"a",issued["token_id"])["revoked_count"],1)

    def test_disconnected_connection_and_removed_owner(self):
        token = self.issue()["token"]
        self.change("UPDATE agent_connections SET status='disconnected'")
        with self.assertRaises(PermissionError):
            self.gateway.ingest(token,self.event())
        self.change("UPDATE agent_connections SET status='connected'")
        self.change("UPDATE members SET removed_at=101 WHERE id=?",(self.owner,))
        self.assertIsNone(self.gateway.authenticate(token))
        with self.assertRaises(PermissionError):
            self.gateway.ingest(token,self.event())
        self.assertEqual(self.count("agent_event_inbox"),0)

    def test_revoke_requires_fresh_dispatcher(self):
        issued = self.issue()
        self.change("UPDATE members SET removed_at=101 WHERE id=?",(self.dispatcher["id"],))
        with self.assertRaises(PermissionError):
            self.gateway.revoke(self.dispatcher,"a",issued["token_id"])
        self.assertIsNotNone(self.gateway.authenticate(issued["token"]))


if __name__ == "__main__":
    unittest.main()
