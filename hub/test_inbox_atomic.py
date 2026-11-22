#!/usr/bin/env python3
"""Inbox atomic send/replay regression using an isolated Hub SQLite database."""
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.messages import post_message, send_inbox_message


class InboxAtomicTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-inbox-atomic-")
        root = Path(self.temp.name)
        self.context = HubContext(
            base_dir=str(root), db_path=str(root / "hub.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(), clock=lambda: 9200.0,
            business_clock=lambda: 9300.0,
        )
        hub.init(self.context)
        self.db = self.context.connect()
        self.dispatcher = dict(self.db.execute(
            "SELECT * FROM members WHERE role='dispatcher'"
        ).fetchone())
        for name, kind in (("worker", None), ("guest", "external")):
            self.db.execute(
                "INSERT INTO members(name,role,token_hash,created_at,kind,"
                "bound_room_id,joined_msg_id) VALUES(?,'member',?,9100,?,?,0)",
                (name, name+"-hash", kind, 1 if kind == "external" else None),
            )
        self.db.execute("INSERT INTO rooms(name,created_at) VALUES('other-room',9100)")
        self.db.commit()
        self.worker = self.member("worker")
        self.guest = self.member("guest")
        self.other_room = self.db.execute(
            "SELECT id FROM rooms WHERE name='other-room'",
        ).fetchone()[0]

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def member(self, name):
        return dict(self.db.execute(
            "SELECT * FROM members WHERE name=?", (name,),
        ).fetchone())

    def send(self, member=None, body="outgoing", op_key="operation-1",
             ignore_new=False, room="pack-main", reply_to_id=0,
             attachment_ids=""):
        member = member or self.dispatcher
        def post(conn, room_name, text, attachments, reply, before_commit):
            return post_message(
                self.context, member, room_name, text, "chat",
                attachments, reply,
                task_ref_re=hub.TASK_REF_RE,
                question_body_limit=hub.QUESTION_BODY_LIMIT,
                validate_question_payload=hub._validate_question_payload,
                question_state=hub._question_state,
                ext_approval_per_task=hub.EXT_APPROVAL_PER_TASK,
                ext_approval_per_hour=hub.EXT_APPROVAL_PER_HOUR,
                ext_msg_retry_after=hub._ext_msg_retry_after,
                ext_rate_error=hub._ext_rate_error,
                approval_gate=lambda *_a, **_k: {},
                connection=conn, before_commit=before_commit,
            )
        return send_inbox_message(
            self.context, member, body, reply_to_id, attachment_ids,
            op_key, ignore_new, room,
            resolve_room=lambda me, room: hub._inbox_resolve_room(
                me, room, self.context
            ),
            read_cursor=hub._read_cursor,
            inbox_msg_sql=hub._INBOX_MSG_SQL,
            inbox_items=hub._inbox_items,
            blind_preview=hub.INBOX_BLIND_REPLY_PREVIEW,
            post_on_connection=post,
        )

    def count(self, table):
        return self.db.execute(
            "SELECT COUNT(*) FROM "+table,
        ).fetchone()[0]

    def seed_message(self, actor, *, room_id=1, created_at=9100):
        cur = self.db.execute(
            "INSERT INTO messages(room_id,author_id,kind,body,created_at) "
            "VALUES(?,?,'chat','incoming',?)",
            (room_id, actor["id"], created_at),
        )
        self.db.commit()
        return cur.lastrowid

    def attachment(self, member=None):
        member = member or self.dispatcher
        cur = self.db.execute(
            "INSERT INTO attachments(sha256,mime,size,original_name,"
            "uploader_id,room_id,created_at) "
            "VALUES('unique-attach','image/png',4,'photo.png',?,1,9100)",
            (member["id"],),
        )
        self.db.commit()
        return cur.lastrowid

    def test_repeat_key_returns_first_message_without_second_write(self):
        first = self.send()
        second = self.send(body="retry with changed text")
        self.assertEqual(first["sent"], True)
        self.assertEqual(second, {
            "sent": True, "message_id": first["message_id"],
            "deduplicated": True,
        })
        self.assertEqual(self.count("messages"), 1)
        self.assertEqual(self.count("message_op_keys"), 1)
        self.assertEqual(self.db.execute(
            "SELECT body FROM messages",
        ).fetchone()[0], "outgoing")
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM audit WHERE action='inbox_send'",
        ).fetchone()[0], 1)

    def test_without_key_each_send_is_independent(self):
        one = self.send(op_key="")
        two = self.send(op_key="")
        self.assertNotEqual(one["message_id"], two["message_id"])
        self.assertEqual(self.count("message_op_keys"), 0)
        self.assertEqual(self.count("messages"), 2)

    def test_op_key_write_failure_rolls_back_message_and_attachment(self):
        aid = self.attachment()
        self.db.execute(
            "CREATE TRIGGER reject_receipt BEFORE INSERT ON message_op_keys "
            "BEGIN SELECT RAISE(ABORT, 'receipt write rejected'); END"
        )
        self.db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "receipt write rejected"):
            self.send(attachment_ids=json.dumps([aid]))
        self.assertEqual(self.count("messages"), 0)
        self.assertEqual(self.count("message_op_keys"), 0)
        self.assertIsNone(self.db.execute(
            "SELECT message_id FROM attachments WHERE id=?", (aid,),
        ).fetchone()[0])
        self.db.execute("DROP TRIGGER reject_receipt")
        self.db.commit()
        self.assertTrue(self.send(attachment_ids=json.dumps([aid]))["sent"])
        self.assertEqual(self.count("messages"), 1)

    def test_audit_write_failure_rolls_back_receipt_and_message(self):
        self.db.execute(
            "CREATE TRIGGER reject_inbox_audit BEFORE INSERT ON audit "
            "WHEN NEW.action='inbox_send' BEGIN "
            "SELECT RAISE(ABORT, 'inbox audit rejected'); END"
        )
        self.db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "inbox audit rejected"):
            self.send()
        self.assertEqual(self.count("messages"), 0)
        self.assertEqual(self.count("message_op_keys"), 0)
        self.db.execute("DROP TRIGGER reject_inbox_audit")
        self.db.commit()
        self.assertTrue(self.send()["sent"])

    def test_block_blind_reply_keeps_key_unused_until_forced(self):
        self.seed_message(self.worker)
        result = self.send()
        self.assertFalse(result["sent"])
        self.assertEqual(result["blocked"], "new_messages")
        self.assertEqual(result["new_count"], 1)
        self.assertEqual(self.count("message_op_keys"), 0)
        self.assertEqual(self.count("messages"), 1)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM audit WHERE action='inbox_send_blind_blocked'",
        ).fetchone()[0], 1)
        forced = self.send(ignore_new=True)
        self.assertTrue(forced["sent"])
        self.assertEqual(self.send()["message_id"], forced["message_id"])

    def test_reply_to_rejects_cross_room_and_missing_message(self):
        target = self.seed_message(self.worker, room_id=self.other_room)
        with self.assertRaisesRegex(ValueError, "in this room"):
            self.send(reply_to_id=target, ignore_new=True)
        with self.assertRaisesRegex(ValueError, "in this room"):
            self.send(reply_to_id=1234567, ignore_new=True)
        self.assertEqual(self.count("message_op_keys"), 0)
        self.assertEqual(self.count("messages"), 1)

    def test_external_join_watermark_rejects_old_reply(self):
        target = self.seed_message(self.dispatcher)
        self.db.execute(
            "UPDATE members SET joined_msg_id=? WHERE id=?",
            (target, self.guest["id"]),
        )
        self.db.commit()
        guest = self.member("guest")
        with self.assertRaisesRegex(ValueError, "join watermark"):
            self.send(member=guest, reply_to_id=target)
        self.assertEqual(self.count("message_op_keys"), 0)

    def test_key_is_scoped_to_member(self):
        first = self.send(op_key="same")
        other = self.send(
            member=self.worker, op_key="same", ignore_new=True
        )
        self.assertNotEqual(first["message_id"], other["message_id"])
        self.assertEqual(self.count("message_op_keys"), 2)

    def test_legacy_null_receipt_is_filled_atomically(self):
        self.db.execute(
            "INSERT INTO message_op_keys(member_id,op_key,message_id,created_at) "
            "VALUES(?,'operation-1',NULL,9100)", (self.dispatcher["id"],)
        )
        self.db.commit()
        result = self.send()
        self.assertEqual(self.db.execute(
            "SELECT message_id FROM message_op_keys",
        ).fetchone()[0], result["message_id"])
        self.assertEqual(self.count("messages"), 1)

    def test_parallel_same_key_has_only_one_message(self):
        barrier = threading.Barrier(3)
        outcomes, errors = [], []
        def attempt():
            barrier.wait(timeout=5)
            try:
                outcomes.append(self.send())
            except Exception as error:
                errors.append(str(error))
        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=8)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(outcomes[0]["message_id"], outcomes[1]["message_id"])
        self.assertEqual(sum(bool(x.get("deduplicated")) for x in outcomes), 1)
        self.assertEqual(self.count("messages"), 1)
        self.assertEqual(self.count("message_op_keys"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
