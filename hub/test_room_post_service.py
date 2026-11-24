#!/usr/bin/env python3
"""Isolated room post tests: permissions, attachments, approval and questions."""
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.messages import post_message


class RoomPostServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pack-room-post-")
        root = Path(self.tmp.name)
        self.context = HubContext(
            base_dir=str(root), db_path=str(root / "hub.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(),
            clock=lambda: 9200.0,
            business_clock=lambda: 9300.0,
        )
        hub.init(self.context)
        self.db = self.context.connect()
        self.dispatcher = dict(self.db.execute(
            "SELECT id,name,role FROM members WHERE role='dispatcher'"
        ).fetchone())
        for name, kind in (("worker", None), ("other", None), ("guest", "external")):
            self.db.execute(
                "INSERT INTO members(name,role,token_hash,created_at,kind,bound_room_id) "
                "VALUES(?,'member',?,9100,?,?)",
                (name, f"{name}-hash", kind, 1 if kind == "external" else None),
            )
        self.db.execute("INSERT INTO rooms(name,created_at) VALUES('separate',9100)")
        self.db.commit()
        self.worker = self.member("worker")
        self.other = self.member("other")
        self.guest = self.member("guest")
        self.other_room_id = self.db.execute(
            "SELECT id FROM rooms WHERE name='separate'"
        ).fetchone()[0]

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def member(self, name):
        return dict(self.db.execute(
            "SELECT id,name,role,kind,bound_room_id FROM members WHERE name=?",
            (name,),
        ).fetchone())

    def post(self, actor=None, room="pack-main", body="hello", kind="chat",
             attachment_ids="", reply_to_id=0, payload="", approval_gate=None):
        def real_gate(conn, rid, mid, text, poster=None):
            return hub._approval_gate(
                conn, rid, mid, text, poster=poster, context=self.context
            )
        return post_message(
            self.context, actor or self.dispatcher, room, body, kind,
            attachment_ids, reply_to_id, payload,
            task_ref_re=hub.TASK_REF_RE,
            question_body_limit=hub.QUESTION_BODY_LIMIT,
            validate_question_payload=hub._validate_question_payload,
            question_state=hub._question_state,
            ext_approval_per_task=hub.EXT_APPROVAL_PER_TASK,
            ext_approval_per_hour=hub.EXT_APPROVAL_PER_HOUR,
            ext_msg_retry_after=hub._ext_msg_retry_after,
            ext_rate_error=hub._ext_rate_error,
            approval_gate=approval_gate or real_gate,
        )

    def make_task(self, status="claimed", room_id=1):
        cursor = self.db.execute(
            "INSERT INTO tasks(room_id,title,body,created_by,assignee,"
            "status,attempts,created_at,updated_at) "
            "VALUES(?,'Test task','Body',?,?,?,1,9100,9100)",
            (room_id, self.dispatcher["id"], self.worker["id"], status),
        )
        self.db.commit()
        return cursor.lastrowid

    def make_attachment(self, actor=None, room_id=1):
        actor = actor or self.dispatcher
        cursor = self.db.execute(
            "INSERT INTO attachments(sha256,mime,size,original_name,"
            "uploader_id,room_id,created_at) "
            "VALUES(?,'image/png',4,'image.png',?,?,9100)",
            (f"sha-{self.db.execute('SELECT COUNT(*) FROM attachments').fetchone()[0]}", actor["id"], room_id),
        )
        self.db.commit()
        return cursor.lastrowid

    def count(self, table):
        return self.db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]

    def test_chat_post_and_attachments_bind_atomically(self):
        a, b = self.make_attachment(), self.make_attachment()
        response = self.post(attachment_ids=json.dumps([a, b]))
        mid = response["message_id"]
        self.assertEqual(tuple(self.db.execute(
            "SELECT body,kind FROM messages WHERE id=?", (mid,),
        ).fetchone()), ("hello", "chat"))
        attached = self.db.execute(
            "SELECT id,message_id FROM attachments ORDER BY id"
        ).fetchall()
        self.assertEqual([tuple(x) for x in attached], [(a, mid), (b, mid)])

    def test_invalid_attachment_rolls_back_message_and_prior_binding(self):
        a = self.make_attachment()
        for attachment_ids in (
            "not-json", "[]", "[true]", "[1,1]", "[1,2,3,4,5]",
            json.dumps([a, 987654]),
        ):
            before = self.count("messages")
            with self.assertRaisesRegex(ValueError, "attachment"):
                self.post(attachment_ids=attachment_ids)
            self.assertEqual(self.count("messages"), before)
            self.assertIsNone(self.db.execute(
                "SELECT message_id FROM attachments WHERE id=?", (a,),
            ).fetchone()[0])

    def test_author_and_room_boundaries_for_external(self):
        self.assertEqual(self.post(actor=self.guest)["message_id"], 1)
        before = self.count("messages")
        with self.assertRaisesRegex(ValueError, "bound room"):
            self.post(actor=self.guest, room="separate")
        self.assertEqual(self.count("messages"), before)
        with self.assertRaisesRegex(ValueError, "not allowed for external"):
            self.post(actor=self.guest, kind="question_answer")
        self.assertEqual(self.count("messages"), before)

    def test_external_question_denial_is_persistently_audited(self):
        with self.assertRaisesRegex(ValueError, "not allowed for external"):
            self.post(actor=self.guest, kind="question", body="test")
        self.assertEqual(self.count("messages"), 0)
        record = self.db.execute(
            "SELECT detail FROM audit WHERE action='external_kind_denied'"
        ).fetchone()
        self.assertIsNotNone(record)
        self.assertIn("kind=question", record[0])

    def test_external_chat_rate_limit_is_audited(self):
        for index in range(hub.EXT_MSG_LIMITS[0][1]):
            self.db.execute(
                "INSERT INTO messages(room_id,author_id,kind,body,created_at) "
                "VALUES(1,?,'chat',?,9199)",
                (self.guest["id"], f"prior {index}"),
            )
        self.db.commit()
        count = self.count("messages")
        with self.assertRaisesRegex(ValueError, "rate_limited"):
            self.post(actor=self.guest)
        self.assertEqual(self.count("messages"), count)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM audit WHERE action='external_rate_limited'"
        ).fetchone()[0], 1)

    def test_only_real_executor_can_request_approval(self):
        tid = self.make_task()
        text = f"申请批准 任务#{tid}"
        with self.assertRaisesRegex(ValueError, "executing that task"):
            self.post(actor=self.other, kind="approval_request", body=text)
        out = self.post(actor=self.worker, kind="approval_request", body=text)
        self.assertEqual(set(out), {"message_id"})
        self.assertEqual(self.db.execute(
            "SELECT task_id FROM messages WHERE id=?", (out["message_id"],),
        ).fetchone()[0], tid)

    def test_approval_request_rejects_ids_outside_sqlite_integer_range(self):
        with self.assertRaisesRegex(ValueError, "approval_request must reference an existing task"):
            self.post(actor=self.worker, kind="approval_request",
                      body="申请批准 任务#9223372036854775808：越界")

    def test_approval_gate_result_shares_transaction(self):
        tid = self.make_task()
        attachment = self.make_attachment()
        def gate(conn, rid, mid, text, poster=None):
            conn.execute(
                "INSERT INTO messages(room_id,author_id,kind,body,created_at) "
                "VALUES(? ,?,'decision','approved',9200)",
                (rid, self.dispatcher["id"]),
            )
            return {"auto_decision": "allow", "task_id": tid}
        out = self.post(
            kind="approval_request", body=f"任务#{tid}",
            attachment_ids=json.dumps([attachment]), approval_gate=gate,
        )
        self.assertEqual(out["auto_decision"], "allow")
        self.assertEqual(self.count("messages"), 2)
        self.assertEqual(self.db.execute(
            "SELECT message_id FROM attachments WHERE id=?", (attachment,),
        ).fetchone()[0], out["message_id"])

    def test_approval_gate_failure_rolls_back_message_and_attachment(self):
        tid = self.make_task()
        aid = self.make_attachment()
        def failed_gate(conn, rid, mid, text, poster=None):
            conn.execute(
                "INSERT INTO messages(room_id,author_id,kind,body,created_at) "
                "VALUES(? ,?,'decision','temporary',9200)",
                (rid, self.dispatcher["id"]),
            )
            raise RuntimeError("approval rejected")
        with self.assertRaisesRegex(RuntimeError, "approval rejected"):
            self.post(
                kind="approval_request", body=f"任务#{tid}",
                attachment_ids=json.dumps([aid]), approval_gate=failed_gate,
            )
        self.assertEqual(self.count("messages"), 0)
        self.assertIsNone(self.db.execute(
            "SELECT message_id FROM attachments WHERE id=?", (aid,),
        ).fetchone()[0])

    def test_question_answer_lifecycle_and_duplicate_guards(self):
        tid = self.make_task()
        payload=json.dumps({"options":["A","B"],"allow_input":False})
        question = self.post(
            actor=self.worker, kind="question",
            body=f"任务#{tid} 请选择", payload=payload,
        )["message_id"]
        with self.assertRaisesRegex(ValueError, "unanswered question"):
            self.post(actor=self.worker, kind="question",
                      body=f"任务#{tid} 再问", payload=payload)
        with self.assertRaisesRegex(ValueError, "dispatcher/owner-only"):
            self.post(actor=self.other, kind="question_answer",
                      body="A", reply_to_id=question)
        with self.assertRaisesRegex(ValueError, "this room"):
            self.post(room="separate", kind="question_answer",
                      body="A", reply_to_id=question)
        answer = self.post(
            kind="question_answer", body="A", reply_to_id=question,
        )["message_id"]
        self.assertEqual(tuple(self.db.execute(
            "SELECT reply_to_id,task_id FROM messages WHERE id=?",
            (answer,),
        ).fetchone()), (question, tid))
        with self.assertRaisesRegex(ValueError, "already answered"):
            self.post(kind="question_answer", body="B", reply_to_id=question)

    def test_question_payload_and_kind_validation_rejects_forgery(self):
        tid = self.make_task()
        for kind in ("task_card", "report", "system", "unknown"):
            with self.assertRaises(ValueError):
                self.post(kind=kind)
        with self.assertRaisesRegex(ValueError, "dispatcher-only"):
            self.post(actor=self.worker, kind="decision")
        with self.assertRaisesRegex(ValueError, "existing task"):
            self.post(actor=self.worker, kind="approval_request", body="no task")
        with self.assertRaisesRegex(ValueError, "payload"):
            self.post(kind="question", body=f"任务#{tid}", payload="{broken")
        with self.assertRaisesRegex(ValueError, "body too long"):
            self.post(kind="question", body="x" * (hub.QUESTION_BODY_LIMIT+1))
        self.assertEqual(self.count("messages"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
