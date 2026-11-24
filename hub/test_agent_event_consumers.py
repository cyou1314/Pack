"""Hub Inbox Consumer lease, cursor, ACK and recovery tests."""
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import hub
import core.migrations as migrations
from core.context import HubContext
from services.agent_event_consumers import (
    ACK_RETRY_WINDOW_SECONDS,
    AgentEventBatchTooLarge,
    AgentEventLeaseBusy,
    AgentEventLeaseExpired,
    AgentEventLeaseUnauthorized,
    ConsumerPrincipal,
    _issue_consumer_principal,
    ack_agent_events,
    claim_agent_events,
    get_agent_event_consumer_cursor,
    release_agent_event_lease,
)
from services.agent_events import append_agent_event


class AgentEventConsumerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-agent-event-consumer-")
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
        self.principal = _issue_consumer_principal("hub-event-projector")
        self.other_principal = _issue_consumer_principal("hub-session-projector")
        connection = self.context.connect()
        try:
            connection.executemany(
                """INSERT INTO agents(
                    agent_id,provider_id,display_name,status,created_at,updated_at
                ) VALUES(?,?,?,'active',1,1)""",
                [
                    ("agent-1", "codex", "Codex"),
                    ("agent-2", "gemini", "Gemini"),
                ],
            )
            connection.commit()
        finally:
            connection.close()
        self.counter = 0

    def tearDown(self):
        self.temp.cleanup()

    def token_factory(self):
        self.counter += 1
        return f"lease-secret-{self.counter:04d}-" + ("x" * 40)

    def connect(self):
        return self.context.connect()

    def append(self, agent_id="agent-1", event_id=None, sequence=1, payload=None):
        event_id = event_id or f"{agent_id}-event-{sequence}"
        event = {
            "eventId": event_id,
            "eventType": "run.progress",
            "providerId": "codex" if agent_id == "agent-1" else "gemini",
            "agentId": agent_id,
            "sequence": sequence,
            "occurredAt": "2026-11-24T03:00:00.000Z",
            "payload": payload if payload is not None else {"progress": sequence},
        }
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            result = append_agent_event(connection, event, received_at=self.now[0])
            connection.commit()
            return result["inboxSeq"]
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def claim(self, principal=None, agent_id="agent-1", **kwargs):
        return claim_agent_events(
            self.context,
            principal or self.principal,
            agent_id,
            token_factory=self.token_factory,
            **kwargs,
        )

    def test_v6_migration_creates_consumer_tables_and_indexes(self):
        connection = self.connect()
        try:
            tables = {
                row["name"] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertTrue({
                "agent_event_consumer_cursors",
                "agent_event_consumer_leases",
                "agent_event_consumer_lease_items",
            }.issubset(tables))
            self.assertEqual(
                connection.execute(
                    "SELECT version,name FROM hub_schema_migrations ORDER BY version"
                ).fetchall()[-1]["version"],
                6,
            )
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            indexes = {
                row["name"] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
            self.assertIn("idx_agent_event_consumer_one_active_lease", indexes)
            self.assertIn("idx_agent_event_consumer_lease_items_pending", indexes)
        finally:
            connection.close()

    def test_v5_upgrade_preserves_existing_inbox_tokens_and_old_checksums(self):
        with tempfile.TemporaryDirectory(prefix="pack-agent-event-v5-upgrade-") as directory:
            root = Path(directory)
            context = HubContext(
                base_dir=str(root),
                db_path=str(root / "hub.db"),
                attachment_dir=str(root / "attachments"),
                lock=threading.RLock(),
                clock=lambda: 1_800_000_000.0,
            )
            original = migrations.MIGRATIONS
            try:
                migrations.MIGRATIONS = original[:-1]
                hub.init(context)
                connection = context.connect()
                try:
                    connection.execute(
                        """INSERT INTO agents(
                            agent_id,provider_id,display_name,status,created_at,updated_at
                        ) VALUES('legacy-agent','codex','Legacy Agent','active',1,1)"""
                    )
                    connection.execute(
                        """INSERT INTO agent_event_tokens(
                            agent_id,token_hash,scope,created_by,created_at,expires_at
                        ) VALUES('legacy-agent','existing-hash','events:write','dispatcher',1,9999999999)"""
                    )
                    connection.commit()
                    event = {
                        "eventId": "pre-v6-event",
                        "eventType": "run.progress",
                        "providerId": "codex",
                        "agentId": "legacy-agent",
                        "sequence": 1,
                        "occurredAt": "2026-11-24T03:00:00.000Z",
                        "payload": {"progress": 25},
                    }
                    connection.execute("BEGIN IMMEDIATE")
                    append_agent_event(connection, event, received_at=10.0)
                    connection.commit()
                    old_checksums = [
                        tuple(row) for row in connection.execute(
                            "SELECT version,name,checksum FROM hub_schema_migrations ORDER BY version"
                        )
                    ]
                    self.assertEqual(len(old_checksums), 5)
                finally:
                    connection.close()

                migrations.MIGRATIONS = original
                hub.init(context)
                connection = context.connect()
                try:
                    new_checksums = [
                        tuple(row) for row in connection.execute(
                            "SELECT version,name,checksum FROM hub_schema_migrations "
                            "WHERE version<=5 ORDER BY version"
                        )
                    ]
                    self.assertEqual(new_checksums, old_checksums)
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM agent_event_inbox WHERE event_id='pre-v6-event'"
                        ).fetchone()[0],
                        1,
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM agent_event_tokens "
                            "WHERE token_hash='existing-hash'"
                        ).fetchone()[0],
                        1,
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT COUNT(*) FROM hub_schema_migrations WHERE version=6"
                        ).fetchone()[0],
                        1,
                    )
                finally:
                    connection.close()
            finally:
                migrations.MIGRATIONS = original

    def test_empty_claim_creates_cursor_but_no_lease(self):
        result = self.claim()
        self.assertTrue(result["empty"])
        self.assertEqual(result["cursor"], 0)
        connection = self.connect()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM agent_event_consumer_leases"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM agent_event_consumer_cursors"
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()

    def test_claim_uses_hub_inbox_order_and_isolated_agent_streams(self):
        first = self.append("agent-1", sequence=1)
        self.append("agent-2", sequence=1)
        third = self.append("agent-1", sequence=2)
        result = self.claim(limit=10)
        self.assertEqual(
            [item["inbox_seq"] for item in result["events"]],
            [first, third],
        )
        self.assertEqual(result["from_inbox_seq"], first)
        self.assertEqual(result["through_inbox_seq"], third)
        self.assertFalse(result["has_more"])

    def test_live_lease_blocks_second_claim_but_other_consumer_is_independent(self):
        self.append()
        first = self.claim()
        with self.assertRaises(AgentEventLeaseBusy):
            self.claim()
        second = self.claim(principal=self.other_principal)
        self.assertNotEqual(first["lease_id"], second["lease_id"])
        self.assertEqual(first["events"][0]["inbox_seq"], second["events"][0]["inbox_seq"])

    def test_claim_concurrency_creates_only_one_active_lease(self):
        self.append()
        barrier = threading.Barrier(2)

        def attempt():
            barrier.wait()
            try:
                return self.claim()
            except AgentEventLeaseBusy:
                return "busy"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(sum(result == "busy" for result in results), 1)
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)

    def test_partial_ack_advances_only_prefix_and_final_ack_completes(self):
        first = self.append(sequence=1)
        self.append(sequence=2)
        third = self.append(sequence=3)
        lease = self.claim(limit=3)

        partial = ack_agent_events(
            self.context, self.principal, lease["lease_id"], lease["lease_token"],
            through_inbox_seq=first,
        )
        self.assertEqual(partial["status"], "active")
        self.assertEqual(partial["acked_inbox_seq"], first)
        repeated = ack_agent_events(
            self.context, self.principal, lease["lease_id"], lease["lease_token"],
            through_inbox_seq=first,
        )
        self.assertTrue(repeated["idempotent"])
        self.assertEqual(repeated["acked_inbox_seq"], first)

        completed = ack_agent_events(
            self.context, self.principal, lease["lease_id"], lease["lease_token"],
            through_inbox_seq=third,
        )
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["acked_inbox_seq"], third)
        self.assertEqual(
            get_agent_event_consumer_cursor(self.context, self.principal, "agent-1")[
                "acked_inbox_seq"
            ],
            third,
        )

    def test_completed_ack_retry_is_read_only_after_original_lease_expiry(self):
        event_seq = self.append()
        lease = self.claim(lease_seconds=5)
        self.now[0] += 1
        ack = ack_agent_events(
            self.context, self.principal, lease["lease_id"], lease["lease_token"],
            through_inbox_seq=event_seq,
        )
        self.assertEqual(ack["status"], "completed")

        # The original 5-second lease has expired, but the completed ACK retry
        # window is still valid and must not advance or otherwise mutate state.
        self.now[0] = lease["expires_at"] + 1
        retry = ack_agent_events(
            self.context, self.principal, lease["lease_id"], lease["lease_token"],
            through_inbox_seq=event_seq,
        )
        self.assertTrue(retry["idempotent"])
        self.assertEqual(retry["acked_inbox_seq"], event_seq)

        self.now[0] = lease["expires_at"] + ACK_RETRY_WINDOW_SECONDS + 2
        with self.assertRaises(AgentEventLeaseExpired):
            ack_agent_events(
                self.context, self.principal, lease["lease_id"], lease["lease_token"],
                through_inbox_seq=event_seq,
            )

    def test_expired_active_lease_redelivers_unacked_events_and_invalidates_old_token(self):
        event_seq = self.append()
        first = self.claim(lease_seconds=5)
        self.now[0] = first["expires_at"] + 1
        second = self.claim(lease_seconds=30)
        self.assertNotEqual(first["lease_id"], second["lease_id"])
        self.assertEqual(second["events"][0]["inbox_seq"], event_seq)
        with self.assertRaises(AgentEventLeaseExpired):
            ack_agent_events(
                self.context, self.principal, first["lease_id"], first["lease_token"],
                through_inbox_seq=event_seq,
            )

    def test_release_does_not_advance_cursor_and_allows_redelivery(self):
        event_seq = self.append()
        first = self.claim()
        released = release_agent_event_lease(
            self.context, self.principal, first["lease_id"], first["lease_token"],
        )
        self.assertEqual(released["status"], "released")
        self.assertEqual(released["cursor"], 0)
        second = self.claim()
        self.assertEqual(second["events"][0]["inbox_seq"], event_seq)
        self.assertNotEqual(first["lease_id"], second["lease_id"])

    def test_foreign_consumer_cannot_ack_or_release_another_consumers_lease(self):
        event_seq = self.append()
        lease = self.claim()
        with self.assertRaises(AgentEventLeaseUnauthorized):
            ack_agent_events(
                self.context, self.other_principal, lease["lease_id"], lease["lease_token"],
                through_inbox_seq=event_seq,
            )
        with self.assertRaises(AgentEventLeaseUnauthorized):
            release_agent_event_lease(
                self.context, self.other_principal, lease["lease_id"], lease["lease_token"],
            )
        self.assertEqual(
            get_agent_event_consumer_cursor(
                self.context, self.other_principal, "agent-1",
            )["acked_inbox_seq"],
            0,
        )

    def test_principal_cannot_be_forged_or_selected_by_caller_supplied_id(self):
        with self.assertRaises(ValueError):
            ConsumerPrincipal("hub-event-projector", object())
        with self.assertRaises(PermissionError):
            _issue_consumer_principal("caller-selected-consumer")
        with self.assertRaises(AgentEventLeaseUnauthorized):
            claim_agent_events(self.context, object(), "agent-1")

    def test_bool_limits_and_ack_sequences_are_rejected(self):
        for kwargs in ({"limit": True}, {"lease_seconds": True}, {"max_bytes": True}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    self.claim(**kwargs)
        self.append()
        lease = self.claim()
        with self.assertRaises(ValueError):
            ack_agent_events(
                self.context, self.principal, lease["lease_id"], lease["lease_token"],
                through_inbox_seq=True,
            )

    def test_oversized_first_event_blocks_without_creating_lease_or_skipping(self):
        self.append(payload={"large": "x" * 2048})
        with self.assertRaises(AgentEventBatchTooLarge):
            self.claim(max_bytes=128)
        connection = self.connect()
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM agent_event_consumer_leases"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT acked_inbox_seq FROM agent_event_consumer_cursors "
                    "WHERE consumer_id='hub-event-projector' AND agent_id='agent-1'"
                ).fetchone(),
                None,
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
