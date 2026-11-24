#!/usr/bin/env python3
"""Durable Hub Agent Event Inbox regression tests."""
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.agent_events import append_agent_event


class AgentEventInboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-agent-event-inbox-")
        root = Path(self.temp.name)
        self.context = HubContext(
            base_dir=str(root),
            db_path=str(root / "hub.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.RLock(),
            clock=time.time,
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
            ) VALUES('agent-2','codex','Codex Two','active',1,1)"""
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def event(self, **overrides):
        value = {
            "eventId": "event-1",
            "eventType": "run.progress",
            "providerId": "codex",
            "agentId": "agent-1",
            "sequence": 1,
            "occurredAt": "2026-11-24T03:00:00.000Z",
            "payload": {"progress": 50, "nested": {"safe": True}},
        }
        value.update(overrides)
        return value

    def append(self, event=None):
        self.db.execute("BEGIN IMMEDIATE")
        return append_agent_event(
            self.db, event or self.event(), received_at=100.0,
        )

    def test_migration_creates_inbox_with_agent_and_provider_foreign_keys(self):
        tables = {
            row["name"] for row in self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertIn("agent_event_inbox", tables)
        self.assertEqual(
            self.db.execute("PRAGMA foreign_key_check").fetchall(), [],
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                """INSERT INTO agent_event_inbox(
                    agent_id,event_id,provider_id,event_type,event_sequence,
                    occurred_at,event_json,received_at
                ) VALUES('missing-agent','bad','codex','run.progress',1,
                         '2026-11-24T03:00:00Z','{}',100)"""
            )
        self.db.rollback()

    def test_append_requires_caller_owned_transaction(self):
        with self.assertRaisesRegex(RuntimeError, "agent_event_transaction_required"):
            append_agent_event(
                self.db, self.event(), received_at=100.0,
            )
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM agent_event_inbox").fetchone()[0],
            0,
        )

    def test_append_is_atomic_and_idempotent_per_agent(self):
        first = self.append()
        self.assertTrue(first["inserted"])
        self.assertTrue(self.db.in_transaction)
        self.db.commit()

        self.db.execute("BEGIN IMMEDIATE")
        duplicate = append_agent_event(
            self.db, self.event(), received_at=101.0,
        )
        self.assertFalse(duplicate["inserted"])
        self.assertEqual(duplicate["inboxSeq"], first["inboxSeq"])
        self.db.commit()

        second_agent = self.event(agentId="agent-2")
        self.db.execute("BEGIN IMMEDIATE")
        second = append_agent_event(
            self.db, second_agent, received_at=102.0,
        )
        self.assertTrue(second["inserted"])
        self.db.commit()
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM agent_event_inbox").fetchone()[0],
            2,
        )

    def test_reusing_event_id_with_different_payload_is_rejected(self):
        self.append()
        self.db.commit()
        self.db.execute("BEGIN IMMEDIATE")
        with self.assertRaisesRegex(ValueError, "event_id_payload_conflict"):
            append_agent_event(
                self.db,
                self.event(payload={"progress": 51}),
                received_at=101.0,
            )
        self.db.rollback()
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM agent_event_inbox").fetchone()[0],
            1,
        )

    def test_identity_and_provider_must_match_registered_agent(self):
        with self.assertRaisesRegex(ValueError, "agent_not_found"):
            self.append(self.event(agentId="missing-agent"))
        self.db.rollback()
        with self.assertRaisesRegex(ValueError, "agent_provider_mismatch"):
            self.append(self.event(providerId="openai"))
        self.db.rollback()

    def test_rejects_sensitive_unknown_and_malformed_event_data(self):
        invalid_events = [
            self.event(payload={"nested": {"private_memory": "do not store"}}),
            self.event(payload={"api-key": "secret"}),
            self.event(payload={"api.key": "secret"}),
            self.event(payload={"private memory": "private"}),
            self.event(payload={"authorization_header": "Bearer secret"}),
            self.event(payload={"secret_token": "do not store"}),
            self.event(payload={"private_key_pem": "do not store"}),
            self.event(payload={"session_token": "do not store"}),
            self.event(authorization="Bearer secret"),
            self.event(sequence=True),
            self.event(sequence=(1 << 63)),
            self.event(occurredAt="not-a-date"),
            self.event(occurredAt="2026-11-24T03:00:00"),
            self.event(eventType="unknown.event"),
        ]
        for event in invalid_events:
            with self.subTest(event=event):
                with self.assertRaises(ValueError):
                    self.append(event)
                self.db.rollback()

    def test_rejects_excessive_payload_depth_and_invalid_unicode(self):
        payload = {}
        current = payload
        for _ in range(30):
            current["child"] = {}
            current = current["child"]
        with self.assertRaisesRegex(ValueError, "event_payload_too_deep"):
            self.append(self.event(payload=payload))
        self.db.rollback()

        with self.assertRaisesRegex(ValueError, "invalid_agent_event_json"):
            self.append(self.event(payload={"value": chr(0xD800)}))
        self.db.rollback()

    def test_rolled_back_transaction_leaves_no_inbox_receipt(self):
        self.append()
        self.db.rollback()
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM agent_event_inbox").fetchone()[0],
            0,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
