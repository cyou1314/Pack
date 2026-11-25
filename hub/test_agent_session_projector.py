"""Atomic Agent Session Inbox projection and Consumer ACK tests."""
import hashlib
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import hub
from core.context import HubContext
from services.agent_event_consumers import (
    AgentEventLeaseUnauthorized,
    _issue_consumer_principal,
    claim_agent_events,
)
from services.agent_events import append_agent_event
from services.agent_session_projector import process_agent_event_lease
from services.agent_sessions import create_agent_session


class AgentSessionProjectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-agent-session-projector-")
        root = Path(self.temp.name)
        self.now = [1_795_564_800.0]
        self.context = HubContext(
            base_dir=str(root),
            db_path=str(root / "hub.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.RLock(),
            clock=lambda: self.now[0],
        )
        hub.init(self.context)
        connection = self.context.connect()
        try:
            connection.execute(
                "UPDATE providers SET status='active' WHERE provider_id='codex'"
            )
            connection.execute(
                """INSERT INTO agents(
                    agent_id,provider_id,display_name,status,created_at,updated_at
                ) VALUES('agent-1','codex','Codex Local','active',1,1)"""
            )
            connection.execute(
                "INSERT OR IGNORE INTO rooms(name,created_at) VALUES('pack-main',1)"
            )
            connection.commit()
        finally:
            connection.close()
        self.principal = _issue_consumer_principal("hub-session-projector")
        self.sequence = 0

    def tearDown(self):
        self.temp.cleanup()

    def connect(self):
        return self.context.connect()

    def create_session(self):
        return create_agent_session(
            self.context,
            {"name": "dispatcher", "role": "dispatcher"},
            "agent-1",
            "pack-main",
        )

    def append(
        self,
        event_type,
        *,
        hub_session_id=None,
        session_id=None,
        run_id=None,
        payload=None,
    ):
        self.sequence += 1
        event = {
            "eventId": f"projector-event-{self.sequence}",
            "eventType": event_type,
            "providerId": "codex",
            "agentId": "agent-1",
            "sequence": self.sequence,
            "occurredAt": "2026-11-25T00:00:00.000Z",
            "payload": payload or {},
        }
        if hub_session_id is not None:
            event["hubSessionId"] = hub_session_id
        if session_id is not None:
            event["sessionId"] = session_id
        if run_id is not None:
            event["runId"] = run_id
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = append_agent_event(
                connection, event, received_at=self.now[0],
            )
            connection.commit()
            return result["inboxSeq"]
        finally:
            connection.close()

    def append_session_created(self, session, provider_session_id="provider-thread-1"):
        return self.append(
            "session.created",
            hub_session_id=session["hub_session_id"],
            session_id=provider_session_id,
            payload={
                "providerSessionId": provider_session_id,
                "resumed": False,
            },
        )

    def claim(self):
        return claim_agent_events(
            self.context,
            self.principal,
            "agent-1",
            limit=100,
            lease_seconds=60,
        )

    def process(self, lease):
        return process_agent_event_lease(
            self.context,
            self.principal,
            lease["lease_id"],
            lease["lease_token"],
        )

    def rows(self, sql, args=()):
        connection = self.connect()
        try:
            return connection.execute(sql, args).fetchall()
        finally:
            connection.close()

    def test_session_message_and_run_events_project_and_ack_in_one_transaction(self):
        session = self.create_session()
        provider_session_id = "provider-thread-1"
        sequences = [
            self.append_session_created(session, provider_session_id),
            self.append(
                "run.started",
                hub_session_id=session["hub_session_id"],
                session_id=provider_session_id,
                run_id="provider-run-1",
                payload={},
            ),
            self.append(
                "message.delta",
                hub_session_id=session["hub_session_id"],
                session_id=provider_session_id,
                run_id="provider-run-1",
                payload={"itemId": "item-1", "delta": "hello "},
            ),
        ]
        final_text = "hello world"
        final_bytes = final_text.encode("utf-8")
        sequences.append(self.append(
            "message.completed",
            hub_session_id=session["hub_session_id"],
            session_id=provider_session_id,
            run_id="provider-run-1",
            payload={
                "itemId": "item-1",
                "phase": "final_answer",
                "textAvailable": True,
                "textIncluded": True,
                "textByteLength": len(final_bytes),
                "textSha256": hashlib.sha256(final_bytes).hexdigest(),
                "text": final_text,
            },
        ))
        sequences.append(self.append(
            "run.completed",
            hub_session_id=session["hub_session_id"],
            session_id=provider_session_id,
            run_id="provider-run-1",
            payload={},
        ))

        lease = self.claim()
        result = self.process(lease)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["acked_inbox_seq"], sequences[-1])
        self.assertEqual(result["processed_count"], 5)
        retry_result = self.process(lease)
        self.assertEqual(retry_result["status"], "completed")
        self.assertTrue(retry_result["idempotent"])
        self.assertEqual(retry_result["acked_inbox_seq"], sequences[-1])

        session_row = self.rows(
            """SELECT provider_session_id,status,last_inbox_seq
               FROM agent_sessions WHERE session_id=?""",
            (session["hub_session_id"],),
        )[0]
        self.assertEqual(
            tuple(session_row),
            (provider_session_id, "active", sequences[-1]),
        )
        message = self.rows(
            """SELECT content,status,provider_run_id,first_inbox_seq,last_inbox_seq
               FROM agent_session_messages
               WHERE session_id=? AND provider_message_id='item-1'""",
            (session["hub_session_id"],),
        )[0]
        self.assertEqual(
            tuple(message),
            (final_text, "completed", "provider-run-1", sequences[2], sequences[3]),
        )
        run = self.rows(
            """SELECT status,started_at,ended_at,first_inbox_seq,last_inbox_seq
               FROM agent_session_runs
               WHERE session_id=? AND provider_run_id='provider-run-1'""",
            (session["hub_session_id"],),
        )[0]
        self.assertEqual(run["status"], "completed")
        self.assertIsNotNone(run["started_at"])
        self.assertIsNotNone(run["ended_at"])
        self.assertEqual(run["first_inbox_seq"], sequences[1])
        self.assertEqual(run["last_inbox_seq"], sequences[-1])

        receipts = self.rows(
            """SELECT inbox_seq,outcome,reason_code FROM agent_event_projection_receipts
               WHERE consumer_id='hub-session-projector' AND agent_id='agent-1'
               ORDER BY inbox_seq"""
        )
        self.assertEqual([row["inbox_seq"] for row in receipts], sequences)
        self.assertEqual([row["outcome"] for row in receipts], ["applied"] * 5)
        self.assertTrue(all(row["reason_code"] is None for row in receipts))
        cursor = self.rows(
            """SELECT acked_inbox_seq FROM agent_event_consumer_cursors
               WHERE consumer_id='hub-session-projector' AND agent_id='agent-1'"""
        )[0]
        self.assertEqual(cursor["acked_inbox_seq"], sequences[-1])
        lease_row = self.rows(
            "SELECT status FROM agent_event_consumer_leases WHERE lease_id=?",
            (lease["lease_id"],),
        )[0]
        self.assertEqual(lease_row["status"], "completed")

    def test_blocked_event_commits_only_successful_prefix_and_can_be_retried(self):
        session = self.create_session()
        first_seq = self.append(
            "message.delta",
            hub_session_id=session["hub_session_id"],
            session_id="provider-thread-1",
            run_id="provider-run-1",
            payload={"itemId": "item-1", "delta": "hello"},
        )
        second_seq = self.append(
            "run.progress",
            hub_session_id=session["hub_session_id"],
            session_id="provider-thread-1",
            run_id="provider-run-1",
            payload={"progress": 50},
        )

        first_lease = self.claim()
        blocked = self.process(first_lease)
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["blocked_inbox_seq"], first_seq)
        self.assertEqual(blocked["reason_code"], "session_not_bound")
        self.assertEqual(blocked["acked_inbox_seq"], 0)
        self.assertEqual(blocked["processed_count"], 0)

        first_receipt = self.rows(
            """SELECT outcome,reason_code,attempt_count
               FROM agent_event_projection_receipts
               WHERE consumer_id='hub-session-projector'
                 AND agent_id='agent-1' AND inbox_seq=?""",
            (first_seq,),
        )[0]
        self.assertEqual(
            tuple(first_receipt),
            ("blocked", "session_not_bound", 1),
        )
        self.assertEqual(
            self.rows(
                """SELECT COUNT(*) AS n FROM agent_event_projection_receipts
                   WHERE consumer_id='hub-session-projector'
                     AND agent_id='agent-1' AND inbox_seq=?""",
                (second_seq,),
            )[0]["n"],
            0,
        )
        self.assertEqual(
            self.rows(
                """SELECT acked_inbox_seq FROM agent_event_consumer_cursors
                   WHERE consumer_id='hub-session-projector' AND agent_id='agent-1'"""
            )[0]["acked_inbox_seq"],
            0,
        )
        self.assertEqual(
            self.rows(
                "SELECT status FROM agent_event_consumer_leases WHERE lease_id=?",
                (first_lease["lease_id"],),
            )[0]["status"],
            "released",
        )

        connection = self.connect()
        try:
            connection.execute(
                """UPDATE agent_sessions SET provider_session_id=?,
                   status='active' WHERE session_id=? AND agent_id='agent-1'""",
                ("provider-thread-1", session["hub_session_id"]),
            )
            connection.commit()
        finally:
            connection.close()

        retry_lease = self.claim()
        retried = self.process(retry_lease)
        self.assertEqual(retried["status"], "completed")
        self.assertEqual(retried["acked_inbox_seq"], second_seq)
        self.assertEqual(retried["processed_count"], 2)
        receipt = self.rows(
            """SELECT outcome,reason_code,attempt_count
               FROM agent_event_projection_receipts
               WHERE consumer_id='hub-session-projector'
                 AND agent_id='agent-1' AND inbox_seq=?""",
            (first_seq,),
        )[0]
        self.assertEqual(tuple(receipt), ("applied", None, 2))
        deferred = self.rows(
            """SELECT outcome,reason_code FROM agent_event_projection_receipts
               WHERE consumer_id='hub-session-projector'
                 AND agent_id='agent-1' AND inbox_seq=?""",
            (second_seq,),
        )[0]
        self.assertEqual(tuple(deferred), ("ignored", "deferred_event_type"))
        message = self.rows(
            """SELECT content,status FROM agent_session_messages
               WHERE session_id=? AND provider_message_id='item-1'""",
            (session["hub_session_id"],),
        )[0]
        self.assertEqual(tuple(message), ("hello", "streaming"))

    def test_legacy_unbound_and_deferred_events_are_ignored_and_acked(self):
        session = self.create_session()
        first_seq = self.append(
            "message.delta",
            session_id="legacy-provider-thread",
            payload={"itemId": "legacy-item", "delta": "legacy text"},
        )
        second_seq = self.append(
            "tool.started",
            hub_session_id=session["hub_session_id"],
            session_id="provider-thread-1",
            run_id="provider-run-1",
            payload={"toolName": "search"},
        )
        lease = self.claim()
        result = self.process(lease)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["acked_inbox_seq"], second_seq)
        receipts = self.rows(
            """SELECT inbox_seq,outcome,reason_code FROM agent_event_projection_receipts
               WHERE consumer_id='hub-session-projector' AND agent_id='agent-1'
               ORDER BY inbox_seq"""
        )
        self.assertEqual(
            [tuple(row) for row in receipts],
            [
                (first_seq, "ignored", "legacy_unbound_session"),
                (second_seq, "ignored", "deferred_event_type"),
            ],
        )
        self.assertEqual(
            self.rows(
                "SELECT COUNT(*) AS n FROM agent_session_messages"
            )[0]["n"],
            0,
        )

    def test_projection_and_ack_rollback_together_when_ack_fails(self):
        session = self.create_session()
        self.append_session_created(session)
        lease = self.claim()

        with mock.patch(
            "services.agent_session_projector.ack_agent_events_in_transaction",
            side_effect=RuntimeError("forced_ack_failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "forced_ack_failure"):
                self.process(lease)

        session_row = self.rows(
            """SELECT provider_session_id,status,last_inbox_seq
               FROM agent_sessions WHERE session_id=?""",
            (session["hub_session_id"],),
        )[0]
        self.assertEqual(
            tuple(session_row),
            (None, "creating", 0),
        )
        self.assertEqual(
            self.rows(
                """SELECT COUNT(*) AS n FROM agent_event_projection_receipts
                   WHERE consumer_id='hub-session-projector' AND agent_id='agent-1'"""
            )[0]["n"],
            0,
        )
        self.assertEqual(
            self.rows(
                """SELECT acked_inbox_seq FROM agent_event_consumer_cursors
                   WHERE consumer_id='hub-session-projector' AND agent_id='agent-1'"""
            )[0]["acked_inbox_seq"],
            0,
        )
        self.assertEqual(
            self.rows(
                "SELECT status FROM agent_event_consumer_leases WHERE lease_id=?",
                (lease["lease_id"],),
            )[0]["status"],
            "active",
        )

    def test_non_projector_consumer_cannot_run_session_projector(self):
        other = _issue_consumer_principal("hub-event-projector")
        with self.assertRaises(AgentEventLeaseUnauthorized):
            process_agent_event_lease(
                self.context, other, "unused-lease", "x" * 48,
            )

    def test_completion_metadata_mismatch_preserves_content_as_incomplete(self):
        session = self.create_session()
        provider_session_id = "provider-thread-1"
        self.append_session_created(session, provider_session_id)
        self.append(
            "run.started",
            hub_session_id=session["hub_session_id"],
            session_id=provider_session_id,
            run_id="provider-run-1",
            payload={},
        )
        self.append(
            "message.delta",
            hub_session_id=session["hub_session_id"],
            session_id=provider_session_id,
            run_id="provider-run-1",
            payload={"itemId": "item-1", "delta": "partial"},
        )
        claimed_text = "different final text"
        claimed_bytes = claimed_text.encode("utf-8")
        last_seq = self.append(
            "message.completed",
            hub_session_id=session["hub_session_id"],
            session_id=provider_session_id,
            run_id="provider-run-1",
            payload={
                "itemId": "item-1",
                "phase": "final_answer",
                "textAvailable": True,
                "textIncluded": False,
                "textByteLength": len(claimed_bytes),
                "textSha256": hashlib.sha256(claimed_bytes).hexdigest(),
            },
        )

        result = self.process(self.claim())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["acked_inbox_seq"], last_seq)
        message = self.rows(
            """SELECT content,status,completed_at FROM agent_session_messages
               WHERE session_id=? AND provider_message_id='item-1'""",
            (session["hub_session_id"],),
        )[0]
        self.assertEqual(message["content"], "partial")
        self.assertEqual(message["status"], "incomplete")
        self.assertIsNone(message["completed_at"])
        receipt = self.rows(
            """SELECT outcome,reason_code FROM agent_event_projection_receipts
               WHERE consumer_id='hub-session-projector' AND agent_id='agent-1'
                 AND inbox_seq=?""",
            (last_seq,),
        )[0]
        self.assertEqual(
            tuple(receipt),
            ("applied", "completion_text_mismatch"),
        )

    def test_run_interrupted_finalizes_message_and_resumed_session_can_reactivate(self):
        session = self.create_session()
        provider_session_id = "provider-thread-1"
        self.append_session_created(session, provider_session_id)
        self.append(
            "run.started",
            hub_session_id=session["hub_session_id"],
            session_id=provider_session_id,
            run_id="provider-run-1",
            payload={},
        )
        self.append(
            "message.delta",
            hub_session_id=session["hub_session_id"],
            session_id=provider_session_id,
            run_id="provider-run-1",
            payload={"itemId": "item-1", "delta": "partial"},
        )
        interrupted_seq = self.append(
            "run.interrupted",
            hub_session_id=session["hub_session_id"],
            session_id=provider_session_id,
            run_id="provider-run-1",
            payload={},
        )
        result = self.process(self.claim())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["acked_inbox_seq"], interrupted_seq)
        run = self.rows(
            """SELECT status FROM agent_session_runs
               WHERE session_id=? AND provider_run_id='provider-run-1'""",
            (session["hub_session_id"],),
        )[0]
        message = self.rows(
            """SELECT status,completed_at FROM agent_session_messages
               WHERE session_id=? AND provider_message_id='item-1'""",
            (session["hub_session_id"],),
        )[0]
        session_row = self.rows(
            "SELECT status FROM agent_sessions WHERE session_id=?",
            (session["hub_session_id"],),
        )[0]
        self.assertEqual(run["status"], "interrupted")
        self.assertEqual(tuple(message), ("interrupted", None))
        self.assertEqual(session_row["status"], "interrupted")

        resumed_seq = self.append(
            "session.created",
            hub_session_id=session["hub_session_id"],
            session_id=provider_session_id,
            payload={
                "providerSessionId": provider_session_id,
                "resumed": True,
            },
        )
        resumed = self.process(self.claim())
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(resumed["acked_inbox_seq"], resumed_seq)
        session_row = self.rows(
            "SELECT status,last_inbox_seq FROM agent_sessions WHERE session_id=?",
            (session["hub_session_id"],),
        )[0]
        self.assertEqual(tuple(session_row), ("active", resumed_seq))

    def test_run_failed_finalizes_only_streaming_messages_as_failed(self):
        session = self.create_session()
        provider_session_id = "provider-thread-1"
        self.append_session_created(session, provider_session_id)
        self.append(
            "run.started",
            hub_session_id=session["hub_session_id"],
            session_id=provider_session_id,
            run_id="provider-run-1",
            payload={},
        )
        self.append(
            "message.delta",
            hub_session_id=session["hub_session_id"],
            session_id=provider_session_id,
            run_id="provider-run-1",
            payload={"itemId": "item-1", "delta": "partial"},
        )
        failed_seq = self.append(
            "run.failed",
            hub_session_id=session["hub_session_id"],
            session_id=provider_session_id,
            run_id="provider-run-1",
            payload={},
        )
        result = self.process(self.claim())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["acked_inbox_seq"], failed_seq)
        run = self.rows(
            """SELECT status FROM agent_session_runs
               WHERE session_id=? AND provider_run_id='provider-run-1'""",
            (session["hub_session_id"],),
        )[0]
        message = self.rows(
            """SELECT status,completed_at FROM agent_session_messages
               WHERE session_id=? AND provider_message_id='item-1'""",
            (session["hub_session_id"],),
        )[0]
        self.assertEqual(run["status"], "failed")
        self.assertEqual(tuple(message), ("failed", None))

    def test_duplicate_provider_session_binding_blocks_second_session(self):
        first = self.create_session()
        second = self.create_session()
        provider_session_id = "provider-thread-duplicate"
        first_seq = self.append_session_created(first, provider_session_id)
        second_seq = self.append_session_created(second, provider_session_id)

        result = self.process(self.claim())
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["blocked_inbox_seq"], second_seq)
        self.assertEqual(result["acked_inbox_seq"], first_seq)
        self.assertEqual(result["reason_code"], "provider_session_id_conflict")

        first_row = self.rows(
            "SELECT provider_session_id,status FROM agent_sessions WHERE session_id=?",
            (first["hub_session_id"],),
        )[0]
        second_row = self.rows(
            "SELECT provider_session_id,status FROM agent_sessions WHERE session_id=?",
            (second["hub_session_id"],),
        )[0]
        self.assertEqual(tuple(first_row), (provider_session_id, "active"))
        self.assertEqual(tuple(second_row), (None, "creating"))
        receipts = self.rows(
            """SELECT inbox_seq,outcome,reason_code
               FROM agent_event_projection_receipts
               WHERE consumer_id='hub-session-projector' AND agent_id='agent-1'
               ORDER BY inbox_seq"""
        )
        self.assertEqual(
            [tuple(row) for row in receipts],
            [
                (first_seq, "applied", None),
                (second_seq, "blocked", "provider_session_id_conflict"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
