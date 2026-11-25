"""Agent Event Gateway pairing, scoped-token and ingestion tests."""
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.agent_event_auth import (
    AgentPairRateLimited,
    authenticate_agent_event_token,
    get_agent_event_capabilities,
    create_agent_event_pair_code,
    redeem_agent_event_pair_code,
    revoke_agent_event_tokens,
)
from services.agent_events import (
    AgentEventRateLimited,
    ingest_agent_event_with_token,
)


class AgentEventGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-agent-event-gateway-")
        root = Path(self.temp.name)
        self.now = [1_800_000_000.0]
        self.context = HubContext(
            base_dir=str(root),
            db_path=str(root / "hub.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.RLock(),
            clock=lambda: self.now[0],
        )
        hub.init(self.context)
        self.db = self.context.connect()
        self.db.execute(
            """INSERT INTO agents(
                agent_id,provider_id,display_name,status,created_at,updated_at
            ) VALUES('agent-1','codex','Codex','active',1,1)"""
        )
        self.db.execute(
            """INSERT INTO agents(
                agent_id,provider_id,display_name,status,created_at,updated_at
            ) VALUES('agent-2','gemini','Gemini','active',1,1)"""
        )
        self.db.commit()
        self.db.close()
        self.counter = 0

    def tearDown(self):
        self.temp.cleanup()

    def token_factory(self):
        self.counter += 1
        return f"generated-secret-{self.counter:04d}-" + ("x" * 40)

    def create_code(self, agent_id="agent-1", ttl_seconds=600):
        return create_agent_event_pair_code(
            self.context,
            {"name": "dispatcher", "role": "dispatcher"},
            agent_id,
            ttl_seconds,
            token_factory=self.token_factory,
            hash_token=hub.hash_token,
        )

    def redeem(self, code):
        return redeem_agent_event_pair_code(
            self.context,
            code,
            token_factory=self.token_factory,
            hash_token=hub.hash_token,
        )

    def connect(self):
        return self.context.connect()

    def event(self, **overrides):
        value = {
            "eventId": "event-1",
            "eventType": "run.progress",
            "sequence": 1,
            "occurredAt": "2026-11-24T03:00:00.000Z",
            "payload": {"progress": 50},
        }
        value.update(overrides)
        return value

    def test_pair_code_is_hashed_single_active_and_audited(self):
        first = self.create_code()
        second = self.create_code()
        db = self.connect()
        try:
            rows = db.execute(
                "SELECT code_hash,revoked_at FROM agent_event_pair_codes ORDER BY code_id"
            ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertNotEqual(rows[0]["code_hash"], first["pair_code"])
            self.assertIsNotNone(rows[0]["revoked_at"])
            self.assertIsNone(rows[1]["revoked_at"])
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM agent_event_pair_codes "
                    "WHERE agent_id='agent-1' AND revoked_at IS NULL AND redeemed_at IS NULL"
                ).fetchone()[0],
                1,
            )
            actions = [
                row["action"] for row in db.execute(
                    "SELECT action FROM audit ORDER BY rowid"
                )
            ]
            self.assertIn("agent_event_pair_code_create", actions)
            self.assertNotIn(first["pair_code"], str(rows))
            self.assertNotIn(second["pair_code"], str(rows))
        finally:
            db.close()

    def test_pair_code_redeems_once_and_issues_agent_scoped_hash_only_token(self):
        pair = self.create_code()
        issued = self.redeem(pair["pair_code"])
        self.assertEqual(issued["agent_id"], "agent-1")
        self.assertEqual(issued["provider_id"], "codex")
        self.assertEqual(issued["scope"], "events:write")
        identity = authenticate_agent_event_token(
            self.context, issued["token"], hash_token=hub.hash_token,
        )
        self.assertEqual(identity["agent_id"], "agent-1")
        self.assertEqual(identity["provider_id"], "codex")

        db = self.connect()
        try:
            row = db.execute(
                "SELECT token_hash,scope FROM agent_event_tokens WHERE token_id=?",
                (issued["token_id"],),
            ).fetchone()
            self.assertNotEqual(row["token_hash"], issued["token"])
            self.assertEqual(row["scope"], "events:write")
            self.assertNotIn(issued["token"], str(dict(row)))
        finally:
            db.close()

        with self.assertRaisesRegex(ValueError, "invalid_agent_pair_code"):
            self.redeem(pair["pair_code"])

    def test_capabilities_require_valid_agent_token_and_advertise_hub_session_field(self):
        pair = self.create_code()
        issued = self.redeem(pair["pair_code"])
        capabilities = get_agent_event_capabilities(
            self.context, issued["token"], hash_token=hub.hash_token,
        )
        self.assertEqual(capabilities["event_envelope_version"], 2)
        self.assertIn("hubSessionId", capabilities["supported_fields"])
        self.assertIn("hub-session-id-envelope-v1", capabilities["features"])

        with self.assertRaises(PermissionError):
            get_agent_event_capabilities(
                self.context, "not-a-valid-token", hash_token=hub.hash_token,
            )
        revoke_agent_event_tokens(
            self.context,
            {"name": "dispatcher"},
            "agent-1",
            issued["token_id"],
        )
        with self.assertRaises(PermissionError):
            get_agent_event_capabilities(
                self.context, issued["token"], hash_token=hub.hash_token,
            )

    def test_pair_code_ttl_and_agent_state_are_enforced(self):
        with self.assertRaisesRegex(ValueError, "invalid_agent_pair_ttl"):
            self.create_code(ttl_seconds=59)
        db = self.connect()
        try:
            db.execute("UPDATE agents SET status='disabled' WHERE agent_id='agent-1'")
            db.commit()
        finally:
            db.close()
        with self.assertRaisesRegex(ValueError, "agent_not_pairable"):
            self.create_code()

    def test_failed_pair_attempts_lock_code_and_global_throttle(self):
        pair = self.create_code()
        self.now[0] += 601
        for _ in range(5):
            with self.assertRaisesRegex(ValueError, "invalid_agent_pair_code"):
                self.redeem(pair["pair_code"])
        db = self.connect()
        try:
            row = db.execute(
                "SELECT fail_count,locked_at FROM agent_event_pair_codes WHERE code_id=?",
                (pair["code_id"],),
            ).fetchone()
            self.assertEqual(row["fail_count"], 5)
            self.assertIsNotNone(row["locked_at"])
        finally:
            db.close()

        for _ in range(15):
            with self.assertRaisesRegex(ValueError, "invalid_agent_pair_code"):
                self.redeem("unknown-code")
        with self.assertRaises(AgentPairRateLimited):
            self.redeem("unknown-code")

    def test_pair_code_expiry_revocation_and_reissue(self):
        pair = self.create_code(ttl_seconds=60)
        self.now[0] += 61
        with self.assertRaisesRegex(ValueError, "invalid_agent_pair_code"):
            self.redeem(pair["pair_code"])
        next_pair = self.create_code()
        issued = self.redeem(next_pair["pair_code"])
        self.assertEqual(issued["agent_id"], "agent-1")

    def test_token_revocation_expiry_and_agent_disable_are_fail_closed(self):
        pair = self.create_code()
        issued = self.redeem(pair["pair_code"])
        revoked = revoke_agent_event_tokens(
            self.context, {"name": "dispatcher"}, "agent-1", issued["token_id"],
        )
        self.assertEqual(revoked["revoked_count"], 1)
        self.assertIsNone(authenticate_agent_event_token(
            self.context, issued["token"], hash_token=hub.hash_token,
        ))

        pair2 = self.create_code()
        issued2 = self.redeem(pair2["pair_code"])
        db = self.connect()
        try:
            db.execute(
                "UPDATE agent_event_tokens SET expires_at=? WHERE token_id=?",
                (self.now[0] - 1, issued2["token_id"]),
            )
            db.commit()
        finally:
            db.close()
        self.assertIsNone(authenticate_agent_event_token(
            self.context, issued2["token"], hash_token=hub.hash_token,
        ))

    def test_ingestion_derives_identity_and_is_idempotent(self):
        pair = self.create_code()
        issued = self.redeem(pair["pair_code"])
        first = ingest_agent_event_with_token(
            self.context, issued["token"], self.event(),
            hash_token=hub.hash_token,
        )
        duplicate = ingest_agent_event_with_token(
            self.context, issued["token"], self.event(),
            hash_token=hub.hash_token,
        )
        self.assertTrue(first["accepted"])
        self.assertFalse(first["duplicate"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["inboxSeq"], first["inboxSeq"])

        with self.assertRaisesRegex(ValueError, "event_id_payload_conflict"):
            ingest_agent_event_with_token(
                self.context, issued["token"],
                self.event(payload={"progress": 51}),
                hash_token=hub.hash_token,
            )
        with self.assertRaisesRegex(ValueError, "agent_identity_mismatch"):
            ingest_agent_event_with_token(
                self.context, issued["token"],
                self.event(agentId="agent-2"),
                hash_token=hub.hash_token,
            )
        with self.assertRaisesRegex(ValueError, "agent_provider_mismatch"):
            ingest_agent_event_with_token(
                self.context, issued["token"],
                self.event(providerId="gemini"),
                hash_token=hub.hash_token,
            )

    def test_duplicate_does_not_consume_rate_budget_and_limit_rolls_back(self):
        issued = self.redeem(self.create_code()["pair_code"])
        ingest_agent_event_with_token(
            self.context, issued["token"], self.event(),
            hash_token=hub.hash_token, max_events_per_minute=1,
        )
        duplicate = ingest_agent_event_with_token(
            self.context, issued["token"], self.event(),
            hash_token=hub.hash_token, max_events_per_minute=1,
        )
        self.assertTrue(duplicate["duplicate"])
        with self.assertRaises(AgentEventRateLimited):
            ingest_agent_event_with_token(
                self.context, issued["token"],
                self.event(eventId="event-2", sequence=2),
                hash_token=hub.hash_token, max_events_per_minute=1,
            )
        db = self.connect()
        try:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM agent_event_inbox").fetchone()[0],
                1,
            )
        finally:
            db.close()

    def test_member_tokens_are_not_accepted_as_agent_event_tokens(self):
        self.assertIsNone(authenticate_agent_event_token(
            self.context, "pk-mem-not-an-agent-token",
            hash_token=hub.hash_token,
        ))
        with self.assertRaisesRegex(PermissionError, "agent_event_unauthorized"):
            ingest_agent_event_with_token(
                self.context, "pk-mem-not-an-agent-token", self.event(),
                hash_token=hub.hash_token,
            )

    def test_http_gateway_routes_enforce_separate_dispatcher_and_agent_credentials(self):
        from starlette.testclient import TestClient

        dispatcher_token = "pk-mem-dispatcher-" + ("d" * 48)
        member_token = "pk-mem-member-" + ("m" * 48)
        readonly_dispatcher_token = "pk-mem-readonly-" + ("r" * 48)
        workspace_dispatcher_token = "pk-mem-workspace-" + ("w" * 48)
        db = self.connect()
        try:
            db.execute(
                "INSERT INTO members(name,role,token_hash,created_at) VALUES(?,?,?,?)",
                ("gateway-dispatcher", "dispatcher", hub.hash_token(dispatcher_token), 1.0),
            )
            db.execute(
                "INSERT INTO members(name,role,token_hash,created_at) VALUES(?,?,?,?)",
                ("gateway-member", "member", hub.hash_token(member_token), 1.0),
            )
            db.execute(
                "INSERT INTO members(name,role,token_hash,created_at,access_mode) VALUES(?,?,?,?,?)",
                ("gateway-readonly-dispatcher", "dispatcher", hub.hash_token(readonly_dispatcher_token), 1.0, "readonly"),
            )
            db.execute(
                "INSERT INTO members(name,role,token_hash,created_at,access_mode) VALUES(?,?,?,?,?)",
                ("gateway-workspace-dispatcher", "dispatcher", hub.hash_token(workspace_dispatcher_token), 1.0, "workspace"),
            )
            db.commit()
        finally:
            db.close()

        server = hub.build_server(self.context)
        with TestClient(server.http_app()) as client:
            denied = client.post(
                "/agent/pair/create",
                json={"agent_id": "agent-1"},
            )
            self.assertEqual(denied.status_code, 401)

            forbidden = client.post(
                "/agent/pair/create",
                headers={"Authorization": f"Bearer {member_token}"},
                json={"agent_id": "agent-1"},
            )
            self.assertEqual(forbidden.status_code, 403)

            readonly_denied = client.post(
                "/agent/pair/create",
                headers={"Authorization": f"Bearer {readonly_dispatcher_token}"},
                json={"agent_id": "agent-1"},
            )
            self.assertEqual(readonly_denied.status_code, 403)

            workspace_denied = client.post(
                "/agent/pair/create",
                headers={"Authorization": f"Bearer {workspace_dispatcher_token}"},
                json={"agent_id": "agent-1"},
            )
            self.assertEqual(workspace_denied.status_code, 403)

            created = client.post(
                "/agent/pair/create",
                headers={"Authorization": f"Bearer {dispatcher_token}"},
                json={"agent_id": "agent-1", "ttl_seconds": 600},
            )
            self.assertEqual(created.status_code, 200, created.text)
            pair_code = created.json()["pair_code"]

            redeemed = client.post(
                "/agent/pair/redeem",
                json={"code": pair_code},
            )
            self.assertEqual(redeemed.status_code, 200, redeemed.text)
            agent_token = redeemed.json()["token"]

            accepted = client.post(
                "/agent/events",
                headers={"Authorization": f"Bearer {agent_token}"},
                json=self.event(),
            )
            self.assertEqual(accepted.status_code, 200, accepted.text)
            self.assertFalse(accepted.json()["duplicate"])

            # Deeply nested JSON can exceed Python's parser recursion limit.
            # The public route must return a bounded client error, not a 500.
            nested_json = (
                '{"eventId":"deep-event","eventType":"run.progress",'
                '"sequence":1,"occurredAt":"2026-11-24T03:00:00.000Z",'
                '"payload":' + ('{"x":' * 10000) + "0" + ("}" * 10000) + "}"
            )
            deep_response = client.post(
                "/agent/events",
                headers={
                    "Authorization": f"Bearer {agent_token}",
                    "Content-Type": "application/json",
                },
                content=nested_json,
            )
            self.assertEqual(deep_response.status_code, 400, deep_response.text)
            self.assertEqual(deep_response.json()["error"], "invalid_agent_event_json")

            duplicate = client.post(
                "/agent/events",
                headers={"Authorization": f"Bearer {agent_token}"},
                json=self.event(),
            )
            self.assertEqual(duplicate.status_code, 200, duplicate.text)
            self.assertTrue(duplicate.json()["duplicate"])

            member_token_denied = client.post(
                "/agent/events",
                headers={"Authorization": f"Bearer {dispatcher_token}"},
                json=self.event(eventId="member-token-event"),
            )
            self.assertEqual(member_token_denied.status_code, 401)

            boolean_token_id = client.post(
                "/agent/tokens/revoke",
                headers={"Authorization": f"Bearer {dispatcher_token}"},
                json={"agent_id": "agent-1", "token_id": False},
            )
            self.assertEqual(boolean_token_id.status_code, 400)
            still_active = client.post(
                "/agent/events",
                headers={"Authorization": f"Bearer {agent_token}"},
                json=self.event(eventId="after-boolean-token-id"),
            )
            self.assertEqual(still_active.status_code, 200, still_active.text)

            revoked = client.post(
                "/agent/tokens/revoke",
                headers={"Authorization": f"Bearer {dispatcher_token}"},
                json={"agent_id": "agent-1", "token_id": redeemed.json()["token_id"]},
            )
            self.assertEqual(revoked.status_code, 200, revoked.text)
            after_revoke = client.post(
                "/agent/events",
                headers={"Authorization": f"Bearer {agent_token}"},
                json=self.event(eventId="after-revoke"),
            )
            self.assertEqual(after_revoke.status_code, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
