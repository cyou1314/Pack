"""Hub-owned Agent Session creation and failure fencing tests."""
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.agent_sessions import (
    create_agent_session,
    fail_agent_session_creation,
)


class AgentSessionBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-agent-session-")
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
        connection = self.context.connect()
        try:
            connection.execute(
                "UPDATE providers SET status='active' WHERE provider_id='codex'"
            )
            connection.executemany(
                """INSERT INTO agents(
                    agent_id,provider_id,display_name,status,created_at,updated_at
                ) VALUES(?,?,?,'active',1,1)""",
                [
                    ("agent-1", "codex", "Codex Local"),
                    ("agent-2", "codex", "Codex API"),
                ],
            )
            connection.execute(
                "INSERT OR IGNORE INTO rooms(name,created_at) VALUES('pack-main',1)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO rooms(name,created_at) VALUES('research',1)"
            )
            connection.commit()
        finally:
            connection.close()

    def tearDown(self):
        self.temp.cleanup()

    def connect(self):
        return self.context.connect()

    def test_dispatcher_creates_hub_session_bound_to_resolved_room(self):
        result = create_agent_session(
            self.context,
            {"id": 1, "name": "dispatcher", "role": "dispatcher"},
            "agent-1",
            "research",
        )
        self.assertEqual(result["status"], "creating")
        self.assertEqual(result["room"], "research")
        self.assertEqual(len(result["hub_session_id"]), 36)

        connection = self.connect()
        try:
            row = connection.execute(
                """SELECT session_id,agent_id,room_id,provider_session_id,status,
                          last_event_sequence
                   FROM agent_sessions WHERE session_id=?""",
                (result["hub_session_id"],),
            ).fetchone()
            room = connection.execute(
                "SELECT id FROM rooms WHERE name='research'"
            ).fetchone()
            self.assertEqual(row["agent_id"], "agent-1")
            self.assertEqual(row["room_id"], room["id"])
            self.assertIsNone(row["provider_session_id"])
            self.assertEqual(row["status"], "creating")
            self.assertEqual(row["last_event_sequence"], 0)
            audit = connection.execute(
                "SELECT actor,action,detail FROM audit WHERE action='agent_session_create'"
            ).fetchone()
            self.assertEqual(audit["actor"], "dispatcher")
            self.assertIn(result["hub_session_id"], audit["detail"])
            self.assertNotIn("token", audit["detail"].lower())
        finally:
            connection.close()

    def test_owner_can_create_but_regular_member_cannot(self):
        owner = create_agent_session(
            self.context,
            {"name": "owner", "role": "owner"},
            "agent-2",
            "pack-main",
        )
        self.assertEqual(owner["status"], "creating")
        with self.assertRaisesRegex(PermissionError, "agent_session_forbidden"):
            create_agent_session(
                self.context,
                {"name": "member", "role": "member"},
                "agent-1",
                "pack-main",
            )

    def test_session_creation_rejects_unknown_or_inactive_agent_provider_and_room(self):
        with self.assertRaisesRegex(ValueError, "agent_not_found"):
            create_agent_session(
                self.context,
                {"name": "dispatcher", "role": "dispatcher"},
                "missing-agent",
                "pack-main",
            )
        with self.assertRaisesRegex(ValueError, "room_not_found"):
            create_agent_session(
                self.context,
                {"name": "dispatcher", "role": "dispatcher"},
                "agent-1",
                "missing-room",
            )

        connection = self.connect()
        try:
            connection.execute(
                "UPDATE agents SET status='disconnected' WHERE agent_id='agent-1'"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(ValueError, "agent_not_active"):
            create_agent_session(
                self.context,
                {"name": "dispatcher", "role": "dispatcher"},
                "agent-1",
                "pack-main",
            )

        connection = self.connect()
        try:
            connection.execute(
                "UPDATE agents SET status='active' WHERE agent_id='agent-1'"
            )
            connection.execute(
                "UPDATE providers SET status='deprecated' WHERE provider_id='codex'"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(ValueError, "agent_provider_not_active"):
            create_agent_session(
                self.context,
                {"name": "dispatcher", "role": "dispatcher"},
                "agent-1",
                "pack-main",
            )

    def test_creation_failure_only_transitions_creating_session(self):
        created = create_agent_session(
            self.context,
            {"name": "dispatcher", "role": "dispatcher"},
            "agent-1",
            "pack-main",
        )
        self.now[0] += 5
        failed = fail_agent_session_creation(
            self.context, created["hub_session_id"], "provider_start_timeout",
        )
        self.assertTrue(failed["changed"])
        self.assertEqual(failed["status"], "failed")

        repeated = fail_agent_session_creation(
            self.context, created["hub_session_id"], "provider_start_timeout",
        )
        self.assertFalse(repeated["changed"])
        self.assertEqual(repeated["status"], "failed")

        connection = self.connect()
        try:
            audit_count = connection.execute(
                """SELECT COUNT(*) FROM audit
                   WHERE action='agent_session_create_failed'"""
            ).fetchone()[0]
            self.assertEqual(audit_count, 1)
        finally:
            connection.close()

    def test_creation_failure_cannot_override_successful_provider_binding(self):
        created = create_agent_session(
            self.context,
            {"name": "dispatcher", "role": "dispatcher"},
            "agent-1",
            "pack-main",
        )
        connection = self.connect()
        try:
            connection.execute(
                """UPDATE agent_sessions SET provider_session_id=?,
                          status='active',updated_at=?
                   WHERE session_id=?""",
                ("provider-thread-1", self.now[0] + 1, created["hub_session_id"]),
            )
            connection.commit()
        finally:
            connection.close()

        result = fail_agent_session_creation(
            self.context, created["hub_session_id"], "late_timeout",
        )
        self.assertFalse(result["changed"])
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["provider_session_id"], "provider-thread-1")

    def test_invalid_reason_code_and_unknown_session_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid_reason_code"):
            fail_agent_session_creation(self.context, "session-id", "unsafe reason")
        with self.assertRaisesRegex(ValueError, "agent_session_not_found"):
            fail_agent_session_creation(
                self.context, "00000000-0000-0000-0000-000000000000",
                "provider_start_timeout",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
