#!/usr/bin/env python3
"""Isolated author-only message edit/delete and transactional audit tests."""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.rooms import delete_message, edit_message, read_new_messages


class MessageMutationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-message-mutation-")
        root = Path(self.temp.name)
        self.context = HubContext(
            base_dir=str(root), db_path=str(root / "hub.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(), clock=lambda: 9200.0,
        )
        hub.init(self.context)
        self.conn = self.context.connect()
        self.author = dict(self.conn.execute(
            "SELECT id,name,role FROM members WHERE role='dispatcher'"
        ).fetchone())
        self.conn.execute(
            "INSERT INTO members(name,role,token_hash,created_at,kind,bound_room_id) "
            "VALUES('guest','member','guest-hash',9200,'external',1)"
        )
        self.conn.execute(
            "INSERT INTO members(name,role,token_hash,created_at) "
            "VALUES('other','member','other-hash',9200)"
        )
        self.conn.execute("INSERT INTO rooms(name,created_at) VALUES('other-room',9200)")
        self.conn.commit()
        self.guest = dict(self.conn.execute(
            "SELECT id,name,kind,bound_room_id FROM members WHERE name='guest'"
        ).fetchone())
        self.other = dict(self.conn.execute(
            "SELECT id,name,kind,bound_room_id FROM members WHERE name='other'"
        ).fetchone())
        self.other_room = self.conn.execute(
            "SELECT id FROM rooms WHERE name='other-room'"
        ).fetchone()[0]

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def make(self, member=None, kind="chat", room_id=1, body="private message"):
        member = member or self.author
        cursor = self.conn.execute(
            "INSERT INTO messages(room_id,author_id,kind,body,created_at) "
            "VALUES(?,?,?,?,9100)",
            (room_id, member["id"], kind, body),
        )
        self.conn.commit()
        return cursor.lastrowid

    def message(self, mid):
        return self.conn.execute(
            "SELECT body,deleted_at,edited_at FROM messages WHERE id=?", (mid,),
        ).fetchone()

    def audit(self, action):
        return self.conn.execute(
            "SELECT actor,detail FROM audit WHERE action=? ORDER BY id",
            (action,),
        ).fetchall()

    def test_edit_changes_body_and_records_id_only_audit(self):
        mid = self.make()
        self.assertEqual(
            edit_message(self.context, self.author, mid, "updated text"),
            {"message_id": mid, "edited": True},
        )
        self.assertEqual(tuple(self.message(mid)), ("updated text", None, 9200.0))
        self.assertEqual(
            [tuple(x) for x in self.audit("message_edit")],
            [(self.author["name"], f"message {mid}")],
        )

    def test_delete_soft_replaces_body_and_redacts_readback(self):
        mid = self.make()
        self.assertEqual(
            delete_message(self.context, self.author, mid),
            {"message_id": mid, "deleted": True},
        )
        self.assertEqual(tuple(self.message(mid)),
                         ("（已删除）", 9200.0, None))
        viewed = read_new_messages(self.context, self.author, "pack-main")
        self.assertEqual(viewed["messages"][-1]["body"], "（已删除）")
        self.assertTrue(viewed["messages"][-1]["deleted"])
        self.assertEqual(
            [tuple(x) for x in self.audit("message_delete")],
            [(self.author["name"], f"message {mid}")],
        )

    def test_no_second_delete_or_edit_after_delete(self):
        mid = self.make()
        delete_message(self.context, self.author, mid)
        with self.assertRaisesRegex(ValueError, "already deleted"):
            delete_message(self.context, self.author, mid)
        with self.assertRaisesRegex(ValueError, "already deleted"):
            edit_message(self.context, self.author, mid, "another")
        self.assertEqual(len(self.audit("message_delete")), 1)

    def test_non_author_cannot_mutate(self):
        mid = self.make()
        with self.assertRaisesRegex(ValueError, "only the author"):
            edit_message(self.context, self.other, mid, "bad")
        with self.assertRaisesRegex(ValueError, "only the author"):
            delete_message(self.context, self.other, mid)
        self.assertEqual(self.message(mid)["body"], "private message")

    def test_external_member_cannot_mutate_outside_bound_room(self):
        mid = self.make(member=self.guest, room_id=self.other_room)
        with self.assertRaisesRegex(ValueError, "outside the bound room"):
            edit_message(self.context, self.guest, mid, "bad")
        with self.assertRaisesRegex(ValueError, "outside the bound room"):
            delete_message(self.context, self.guest, mid)
        valid = self.make(member=self.guest)
        self.assertTrue(edit_message(self.context, self.guest, valid, "ok")["edited"])

    def test_structural_messages_are_immutable_and_missing_rejected(self):
        for kind in ("task_card", "report", "approval_request", "system"):
            mid = self.make(kind=kind)
            with self.assertRaisesRegex(ValueError, "only chat messages"):
                delete_message(self.context, self.author, mid)
            with self.assertRaisesRegex(ValueError, "only chat messages"):
                edit_message(self.context, self.author, mid, "bad")
        with self.assertRaisesRegex(ValueError, "no such message"):
            delete_message(self.context, self.author, 999999)
        with self.assertRaisesRegex(ValueError, "no such message"):
            edit_message(self.context, self.author, 999999, "bad")

    def test_edit_input_limits(self):
        mid = self.make()
        for value in ("", "  ", "x" * 20001):
            with self.assertRaises(ValueError):
                edit_message(self.context, self.author, mid, value)
        self.assertEqual(self.message(mid)["body"], "private message")

    def test_audit_failure_rolls_back_edit_and_delete(self):
        self.conn.execute(
            "CREATE TRIGGER reject_message_audit BEFORE INSERT ON audit "
            "WHEN NEW.action IN ('message_edit','message_delete') "
            "BEGIN SELECT RAISE(ABORT, 'message audit rejected'); END"
        )
        self.conn.commit()
        mid = self.make()
        original = tuple(self.message(mid))
        with self.assertRaisesRegex(sqlite3.IntegrityError, "message audit rejected"):
            edit_message(self.context, self.author, mid, "changed")
        self.assertEqual(tuple(self.message(mid)), original)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "message audit rejected"):
            delete_message(self.context, self.author, mid)
        self.assertEqual(tuple(self.message(mid)), original)
        self.assertEqual(len(self.audit("message_edit")), 0)
        self.assertEqual(len(self.audit("message_delete")), 0)

    def test_concurrent_soft_delete_has_only_one_winner(self):
        mid = self.make()
        barrier = threading.Barrier(3)
        wins, errors = [], []
        def attempt():
            barrier.wait(timeout=5)
            try:
                wins.append(delete_message(self.context, self.author, mid))
            except ValueError as error:
                errors.append(str(error))
        workers = [threading.Thread(target=attempt) for _ in range(2)]
        for worker in workers:
            worker.start()
        barrier.wait(timeout=5)
        for worker in workers:
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
        self.assertEqual(len(wins), 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("already deleted", errors[0])
        self.assertEqual(len(self.audit("message_delete")), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
