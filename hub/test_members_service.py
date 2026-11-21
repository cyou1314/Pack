#!/usr/bin/env python3
"""Contract tests for member read service in isolated HubContext."""
import json
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.members import list_members


class MemberServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-members-service-")
        root = Path(self.temp.name)
        self.context = HubContext(
            base_dir=str(root), db_path=str(root / "members.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(), clock=lambda: 1_800_000_000.0,
        )
        hub.init(self.context)
        self.connection = self.context.connect()
        self.dispatcher_id = self.connection.execute(
            "SELECT id FROM members WHERE role='dispatcher'"
        ).fetchone()["id"]

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def read(self, me):
        return list_members(
            self.context, me, avatar_for=hub.avatar_for,
            week_start=hub._week_start_cst, access_modes=hub.ACCESS_MODES,
        )["members"]

    def test_member_list_defaults_and_completed_week_counts(self):
        now = self.context.clock()
        self.connection.execute(
            "UPDATE members SET avatar=?, access_mode=?, quota_week_used_pct=? "
            "WHERE id=?", ("invalid json", "unknown", 82.0, self.dispatcher_id)
        )
        self.connection.execute(
            "INSERT INTO tasks(room_id,title,body,created_by,assignee,status,"
            "created_at,updated_at) VALUES(1,'done task','',?,?, 'done',?,?)",
            (self.dispatcher_id, self.dispatcher_id, now, now)
        )
        self.connection.commit()
        members = self.read({"kind":"internal"})
        self.assertEqual(len(members), 1)
        row = members[0]
        self.assertEqual(row["week_done"], 1)
        self.assertEqual(row["access_mode"], "full")
        self.assertEqual(row["kind"], "internal")
        self.assertEqual(row["display_name"], row["name"])
        self.assertIsInstance(row["avatar"], dict)
        self.assertEqual(row["quota_week_used_pct"], 82.0)

    def test_external_member_view_removes_private_quota_login_and_access(self):
        self.connection.execute(
            "UPDATE members SET quota_week_used_pct=75, login_state='logged_in',"
            "access_mode='readonly' WHERE id=?", (self.dispatcher_id,)
        )
        self.connection.commit()
        members = self.read({"kind":"external","bound_room_id":1})
        self.assertEqual(len(members), 1)
        row = members[0]
        self.assertEqual(
            set(row), {"name", "display_name", "role", "kind", "provider",
                       "avatar", "last_heartbeat"}
        )
        self.assertNotIn("quota_week_used_pct", row)
        self.assertNotIn("login_state", row)
        self.assertNotIn("access_mode", row)
        self.assertNotIn("week_done", row)


if __name__ == "__main__":
    unittest.main(verbosity=2)
