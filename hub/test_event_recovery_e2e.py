"""Real Node -> loopback HTTP fixture -> bound Gateway -> Session projection.

Test-only listener: no production routes, real model requests or user data.
"""
import hashlib
import json
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import hub
from event_gateway_test_context import HubContext
from collaboration_store import initialize, write_transaction
from collaboration_connection_binding import SCHEMA, bind_connection
from agent_event_gateway_boundary import BoundEventGateway
from registry_consumer_v6_port import apply_consumer_v6
from registry_session_v7_port import apply_session_v7
from agent_sessions import create_agent_session
from agent_event_consumers import (
    _issue_consumer_principal, claim_agent_events, AgentEventLeaseExpired,
)
from agent_session_projector import process_agent_event_lease

CLIENT = Path(__file__).resolve().parents[1] / "runtime/tests/fixtures/event-recovery-client.mjs"
ACCOUNT = "11111111-1111-4111-8111-111111111111"


class EventRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.node = shutil.which("node")
        self.assertIsNotNone(self.node, "Node 22 is required for cross-runtime integration")
        tmp = tempfile.TemporaryDirectory(prefix="pack-e2e-event-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.outbox = root / "outbox.sqlite"
        self.now = [100.0]
        self.context = HubContext(str(root), str(root/"hub.db"), str(root/"attachments"),
                                  threading.RLock(), lambda: self.now[0])
        self.context.initialize(hub)
        self.gateway = BoundEventGateway(self.context)
        conn = self.context.connect()
        try:
            apply_consumer_v6(conn, now=100)
            apply_session_v7(conn, now=100)
            initialize(conn)
            with write_transaction(conn):
                self.dispatcher = dict(conn.execute(
                    "SELECT id,name,role FROM members WHERE role='dispatcher'").fetchone())
                owner = conn.execute(
                    "INSERT INTO members(name,role,token_hash,created_at) "
                    "VALUES('owner','member','owner-fixture',1)").lastrowid
                member = conn.execute(
                    "INSERT INTO members(name,role,token_hash,created_at,provider,access_mode) "
                    "VALUES('worker','member','worker-fixture',1,'chatgpt','full')").lastrowid
                conn.execute("INSERT INTO collaboration_executors VALUES(?,?,'chatgpt','native',?,0)",
                             (member,owner,ACCOUNT))
                conn.execute(
                    "INSERT INTO agents(agent_id,provider_id,legacy_member_id,display_name,status,created_at,updated_at) "
                    "VALUES('a','openai',?,'Fixture','active',1,1)",(member,))
                conn.execute(
                    "INSERT INTO agent_connections(connection_id,agent_id,mode,auth_kind,status,created_at,updated_at) "
                    "VALUES('c','a','native_account','none','connected',1,1)")
                conn.execute(SCHEMA)
                bind_connection(conn,actor_id=owner,member_id=member,
                                account_ref=ACCOUNT,agent_id="a",connection_id="c")
        finally:
            conn.close()
        # Trusted test host creates a session; no client-supplied actor or room.
        sid = create_agent_session(self.context,self.dispatcher,"a","pack-main")["hub_session_id"]
        final = "hello world"
        kinds = [
            ("session.created", {"providerSessionId":"thread-1","resumed":False}),
            ("run.started", {}),
            ("message.delta", {"itemId":"item-1","delta":"hello "}),
            ("message.completed", {"itemId":"item-1","phase":"final_answer",
                "textAvailable":True,"textIncluded":True,"text":final,
                "textByteLength":len(final.encode()),"textSha256":hashlib.sha256(final.encode()).hexdigest()}),
            ("run.completed", {}),
        ]
        self.events = []
        for seq,(kind,payload) in enumerate(kinds,1):
            event = dict(eventId=f"event-{seq}",eventType=kind,providerId="openai",
                         agentId="a",sequence=seq,occurredAt="2026-01-01T00:00:00Z",
                         hubSessionId=sid,sessionId="thread-1",payload=payload)
            if kind != "session.created":
                event["runId"] = "run-1"
            self.events.append(event)
        self.principal = _issue_consumer_principal("hub-session-projector")
        self.drop_next = False
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                if self.path != "/agent/events":
                    self.send_error(404)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 256 * 1024:
                        self.send_error(413)
                        return
                    bearer = self.headers.get("Authorization", "")
                    if not bearer.startswith("Bearer "):
                        raise PermissionError()
                    receipt = fixture.gateway.ingest(bearer[7:],json.loads(self.rfile.read(size)))
                except PermissionError:
                    self.send_error(401)
                    return
                except (ValueError, TypeError):
                    self.send_error(400)
                    return
                if fixture.drop_next:
                    fixture.drop_next = False
                    # Commit succeeded, but the client gets no HTTP receipt.
                    self.close_connection = True
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    return
                body = json.dumps(receipt).encode()
                self.send_response(200)
                self.send_header("Content-Type","application/json")
                self.send_header("Content-Length",str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        thread.start()
        def stop():
            self.server.shutdown()
            self.server.server_close()
            thread.join(timeout=5)
        self.addCleanup(stop)
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}/agent/events"

    def issue(self):
        return self.gateway.redeem(self.gateway.create_pair(self.dispatcher,"a")["pair_code"])

    def client(self, mode, token):
        # Independent Node process for each call, same durable Outbox file.
        proc = subprocess.run([self.node,str(CLIENT)],input=json.dumps({
            "databasePath":str(self.outbox),"endpoint":self.endpoint,"token":token,
            "events":self.events,"mode":mode,
        }),text=True,capture_output=True,timeout=45)
        self.assertEqual(proc.returncode,0,proc.stderr)
        return json.loads(proc.stdout)

    def rows(self, sql):
        conn = self.context.connect()
        try:
            return [tuple(r) for r in conn.execute(sql)]
        finally:
            conn.close()

    def claim(self):
        return claim_agent_events(self.context,self.principal,"a",lease_seconds=5)

    def project(self, lease):
        return process_agent_event_lease(self.context,self.principal,
                                        lease["lease_id"],lease["lease_token"])

    def assert_projected(self, last_seq):
        self.assertEqual(self.rows("SELECT provider_session_id,status,last_inbox_seq FROM agent_sessions"),
                         [("thread-1","active",last_seq)])
        self.assertEqual(self.rows("SELECT content,status FROM agent_session_messages"),
                         [("hello world","completed")])
        self.assertEqual(self.rows("SELECT status FROM agent_session_runs"),[("completed",)])
        self.assertEqual(self.rows("SELECT acked_inbox_seq FROM agent_event_consumer_cursors"),[(last_seq,)])
        self.assertEqual(self.rows("SELECT outcome,COUNT(*) FROM agent_event_projection_receipts GROUP BY outcome"),
                         [("applied",5)])
        self.assertTrue(self.claim()["empty"])

    def test_lost_receipt_process_restart_and_projection_rollback(self):
        token = self.issue()["token"]
        self.drop_next = True
        self.assertEqual(self.client("lost",token)["status"],"retry_scheduled")
        self.assertFalse(self.drop_next)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM agent_event_inbox"),[(1,)])
        receipts = self.client("retry",token)["receipts"]
        # Duplicate insert attempts may consume SQLite AUTOINCREMENT numbers.
        self.assertTrue(receipts[0]["duplicate"])
        self.assertTrue(all(not r["duplicate"] for r in receipts[1:]))
        sequences = [r["inboxSeq"] for r in receipts]
        self.assertEqual(sequences,sorted(set(sequences)))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM agent_event_inbox"),[(5,)])
        lease = self.claim()
        with mock.patch("agent_session_projector.ack_agent_events_in_transaction",
                        side_effect=RuntimeError("fixture_ack_failure")):
            with self.assertRaisesRegex(RuntimeError,"fixture_ack_failure"):
                self.project(lease)
        self.assertEqual(self.rows("SELECT provider_session_id,status,last_inbox_seq FROM agent_sessions"),
                         [(None,"creating",0)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM agent_session_messages"),[(0,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM agent_event_projection_receipts"),[(0,)])
        self.assertEqual(self.rows("SELECT acked_inbox_seq FROM agent_event_consumer_cursors"),[(0,)])
        self.now[0] += 6
        replacement = self.claim()
        with self.assertRaises(AgentEventLeaseExpired):
            self.project(lease)
        result = self.project(replacement)
        self.assertEqual(result["acked_inbox_seq"],sequences[-1])
        self.assertTrue(self.project(replacement)["idempotent"])
        self.assert_projected(sequences[-1])

    def test_revoked_token_pauses_without_loss_and_fresh_token_recovers(self):
        issued = self.issue()
        self.gateway.revoke(self.dispatcher,"a",issued["token_id"])
        self.assertEqual(self.client("denied",issued["token"])["status"],"paused")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM agent_event_inbox"),[(0,)])
        receipts = self.client("resume",self.issue()["token"])["receipts"]
        self.assertTrue(all(not r["duplicate"] for r in receipts))
        self.assertEqual(self.project(self.claim())["status"],"completed")
        self.assert_projected(receipts[-1]["inboxSeq"])


if __name__ == "__main__":
    unittest.main()
