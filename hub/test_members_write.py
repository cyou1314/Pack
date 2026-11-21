#!/usr/bin/env python3
"""Member management tests with an isolated database and audit ledger."""
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.members import rename_member, set_access_mode, set_login_state


class MemberWriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-member-write-")
        root = Path(self.temp.name)
        self.context = HubContext(
            base_dir=str(root), db_path=str(root / "member.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(), clock=lambda: 9000.0,
        )
        hub.init(self.context)
        self.actor = {"name": "dispatcher"}
        self.db = self.context.connect()
        self.db.execute(
            "INSERT INTO members(name,role,token_hash,created_at,provider) "
            "VALUES('worker','member','dummy-hash',9000,'codex')"
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_access_mode_blocks_privileged_accounts_and_preserves_previous_value(self):
        self.assertEqual(
            set_access_mode(self.context,self.actor,"worker","readonly",hub.ACCESS_MODES),
            {"name":"worker","access_mode":"readonly"},
        )
        with self.assertRaisesRegex(ValueError,"bad access mode"):
            set_access_mode(self.context,self.actor,"worker","invalid",hub.ACCESS_MODES)
        with self.assertRaisesRegex(ValueError,"dispatcher"):
            set_access_mode(self.context,self.actor,"dispatcher","readonly",hub.ACCESS_MODES)
        with self.assertRaisesRegex(ValueError,"no such member"):
            set_access_mode(self.context,self.actor,"missing","full",hub.ACCESS_MODES)
        self.assertEqual(
            self.db.execute("SELECT access_mode FROM members WHERE name='worker'").fetchone()[0],
            "readonly",
        )

    def test_login_state_rejects_invalid_and_removed_members(self):
        result = set_login_state(
            self.context,self.actor,"worker","logged_in",hub.LOGIN_STATES
        )
        self.assertEqual(result, {"name":"worker","provider":"codex","login_state":"logged_in"})
        with self.assertRaisesRegex(ValueError,"bad login_state"):
            set_login_state(self.context,self.actor,"worker","unknown",hub.LOGIN_STATES)
        self.db.execute("UPDATE members SET removed_at=9001 WHERE name='worker'")
        self.db.commit()
        with self.assertRaisesRegex(ValueError,"member removed"):
            set_login_state(self.context,self.actor,"worker","expired",hub.LOGIN_STATES)
        self.assertEqual(
            self.db.execute("SELECT login_state FROM members WHERE name='worker'").fetchone()[0],
            "logged_in",
        )

    def test_rename_changes_display_only_and_records_audit(self):
        result=rename_member(self.context,self.actor,"worker","  New Worker  ")
        self.assertEqual(result,{"name":"worker","display_name":"New Worker"})
        row=self.db.execute(
            "SELECT name,display_name FROM members WHERE name='worker'"
        ).fetchone()
        self.assertEqual(tuple(row),("worker","New Worker"))
        with self.assertRaisesRegex(ValueError,"1-80 chars"):
            rename_member(self.context,self.actor,"worker"," ")
        with self.assertRaisesRegex(ValueError,"no such member"):
            rename_member(self.context,self.actor,"missing","Other")
        audit=self.db.execute(
            "SELECT action,detail FROM audit WHERE action='member_rename' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(tuple(audit),("member_rename","worker -> New Worker"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
