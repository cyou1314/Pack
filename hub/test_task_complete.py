#!/usr/bin/env python3
"""Isolated regression tests for the task completion service."""
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.tasks import claim_task, complete_task, create_task


class TaskCompleteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-task-complete-")
        root = Path(self.temp.name)
        self.context = HubContext(
            base_dir=str(root), db_path=str(root / "task.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(), clock=lambda: 9200.0,
            business_clock=lambda: 9300.0,
        )
        hub.init(self.context)
        self.conn = self.context.connect()
        self.dispatcher = dict(self.conn.execute(
            "SELECT id,name FROM members WHERE role='dispatcher'"
        ).fetchone())
        self.conn.execute(
            "INSERT INTO members(name,role,token_hash,created_at) "
            "VALUES('worker','member','worker-hash',9200)"
        )
        self.conn.execute(
            "INSERT INTO members(name,role,token_hash,created_at,kind,bound_room_id) "
            "VALUES('guest','member','guest-hash',9200,'external',1)"
        )
        self.conn.execute(
            "INSERT INTO rooms(name,created_at) VALUES('other-room',9200)"
        )
        self.conn.commit()
        self.worker = self.member("worker")
        self.guest = self.member("guest")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def member(self, name):
        return dict(self.conn.execute(
            "SELECT id,name,role,kind,bound_room_id FROM members WHERE name=?",
            (name,),
        ).fetchone())

    def make_claim(self, member=None):
        member = member or self.worker
        task_id = create_task(
            self.context, self.dispatcher, "pack-main", "Deliver", "Body",
            member["name"], is_executor_pinned=hub._is_executor_pinned,
        )["task_id"]
        def release(conn, tid, actor, reason):
            return hub._desktop_release_for_task(
                conn, tid, actor, reason, context=self.context
            )
        token = claim_task(
            self.context, member, task_id, lease_seconds=1800,
            is_executor_pinned=hub._is_executor_pinned,
            desktop_release_for_task=release,
        )["claim_token"]
        return task_id, token

    def complete(self, tid, token, result="Delivered", envelope=None,
                 attachment_ids="", member=None):
        def close_run(conn, tid, outcome, actor, detail=""):
            return hub._close_run(
                conn, tid, outcome, actor, detail, context=self.context
            )
        def release(conn, tid, actor, reason):
            return hub._desktop_release_for_task(
                conn, tid, actor, reason, context=self.context
            )
        return complete_task(
            self.context, member or self.worker, tid, token, result,
            envelope, attachment_ids, validate_envelope=hub._validate_envelope,
            close_run=close_run, desktop_release_for_task=release,
        )

    def state(self, tid):
        return tuple(self.conn.execute(
            "SELECT status,result,envelope,claim_token,lease_expires,updated_at "
            "FROM tasks WHERE id=?", (tid,),
        ).fetchone())

    def reports(self, tid):
        return self.conn.execute(
            "SELECT * FROM messages WHERE task_id=? AND kind='report'", (tid,)
        ).fetchall()

    def seed_run_and_lock(self, tid, token):
        self.conn.execute(
            "INSERT INTO runs(task_id,member_id,fence,claim_token,status,started_at) "
            "VALUES(?,?,1,?,'running',9100)",
            (tid, self.worker["id"], token),
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO desktop_lock(id,holder,task_id,acquired_at) "
            "VALUES(1,'worker',?,9100)", (tid,),
        )
        self.conn.commit()

    def attachment(self, uploader=None):
        uploader = uploader or self.worker
        cur = self.conn.execute(
            "INSERT INTO attachments(sha256,mime,size,original_name,"
            "uploader_id,room_id,created_at) "
            "VALUES('abc','image/png',1,'image.png',?,1,9100)",
            (uploader["id"],),
        )
        self.conn.commit()
        return cur.lastrowid

    def test_plain_completion_closes_run_and_lock(self):
        tid, token = self.make_claim()
        self.seed_run_and_lock(tid, token)
        self.assertEqual(self.complete(tid, token), {"task_id": tid, "status": "review"})
        self.assertEqual(self.state(tid), ("review", "Delivered", None, None, None, 9200.0))
        self.assertEqual(self.reports(tid)[0]["body"], f"交活 任务#{tid}: Delivered")
        self.assertEqual(tuple(self.conn.execute(
            "SELECT status,outcome,ended_at FROM runs WHERE task_id=?", (tid,)
        ).fetchone()), ("closed", "completed", 9200.0))
        self.assertEqual(tuple(self.conn.execute(
            "SELECT holder,task_id FROM desktop_lock WHERE id=1"
        ).fetchone()), (None, None))
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM audit WHERE action='task_complete'"
        ).fetchone()[0], 1)
        with self.assertRaisesRegex(ValueError, "claim_token mismatch"):
            self.complete(tid, token)

    def test_structured_envelope_persists_summary_and_verdict(self):
        tid, token = self.make_claim()
        envelope = {"verdict":"done", "summary":"shipped", "artifacts":[]}
        out = self.complete(tid, token, envelope=envelope)
        self.assertEqual(out["verdict"], "done")
        self.assertEqual(out["envelope"]["summary"], "shipped")
        self.assertEqual(self.state(tid)[1], "shipped")
        self.assertEqual(json.loads(self.state(tid)[2]), out["envelope"])
        self.assertIn("shipped", self.reports(tid)[0]["body"])
        self.assertEqual(self.conn.execute(
            "SELECT detail FROM audit WHERE action='task_complete'"
        ).fetchone()[0], f"task {tid} verdict=done")

    def test_invalid_envelope_rejects_without_writes(self):
        tid, token = self.make_claim()
        before = self.state(tid)
        with self.assertRaises(ValueError) as caught:
            self.complete(tid, token, envelope={"verdict":"done"})
        payload = json.loads(str(caught.exception))
        self.assertEqual(payload["error"], "invalid_envelope")
        self.assertEqual(payload["task_id"], tid)
        self.assertEqual(self.state(tid), before)
        self.assertEqual(len(self.reports(tid)), 0)

    def test_attachment_binding_and_audit(self):
        tid, token = self.make_claim()
        aid = self.attachment()
        out = self.complete(tid, token, attachment_ids=json.dumps([aid]))
        self.assertEqual(out["attachments"], 1)
        self.assertEqual(self.conn.execute(
            "SELECT message_id FROM attachments WHERE id=?", (aid,)
        ).fetchone()[0], self.reports(tid)[0]["id"])
        self.assertEqual(self.conn.execute(
            "SELECT detail FROM audit WHERE action='task_complete'"
        ).fetchone()[0], f"task {tid} attachments=1")

    def test_bad_attachment_rolls_back_partially_bound_rows(self):
        tid, token = self.make_claim()
        valid = self.attachment()
        foreign = self.attachment(self.guest)
        for ids, pattern in [
            ("{invalid", "JSON array"),
            (json.dumps([valid, valid]), "unique integer ids"),
            (json.dumps([True]), "unique integer ids"),
            (json.dumps([valid, foreign]), "cannot be bound"),
        ]:
            old = self.state(tid)
            with self.assertRaisesRegex(ValueError, pattern):
                self.complete(tid, token, attachment_ids=ids)
            self.assertEqual(self.state(tid), old)
            self.assertEqual(len(self.reports(tid)), 0)
            self.assertIsNone(self.conn.execute(
                "SELECT message_id FROM attachments WHERE id=?", (valid,)
            ).fetchone()[0])

    def test_message_failure_rolls_back_run_lock_and_task(self):
        tid, token = self.make_claim()
        self.seed_run_and_lock(tid, token)
        old = self.state(tid)
        self.conn.execute(
            "CREATE TRIGGER reject_report BEFORE INSERT ON messages "
            "WHEN NEW.kind='report' BEGIN "
            "SELECT RAISE(ABORT, 'report rejected'); END"
        )
        self.conn.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "report rejected"):
            self.complete(tid, token)
        self.assertEqual(self.state(tid), old)
        self.assertEqual(self.conn.execute(
            "SELECT status FROM runs WHERE task_id=?", (tid,)
        ).fetchone()[0], "running")
        self.assertEqual(self.conn.execute(
            "SELECT holder FROM desktop_lock WHERE id=1"
        ).fetchone()[0], "worker")
        self.assertEqual(len(self.reports(tid)), 0)

    def test_wrong_token_and_external_cross_room_rejected(self):
        tid, token = self.make_claim()
        with self.assertRaisesRegex(ValueError, "claim_token mismatch"):
            self.complete(tid, "stale")
        other, remote_token = self.make_claim(self.guest)
        self.conn.execute(
            "UPDATE tasks SET room_id=(SELECT id FROM rooms WHERE name='other-room') "
            "WHERE id=?", (other,)
        )
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "outside the bound room"):
            self.complete(other, remote_token, member=self.guest)
        self.assertEqual(self.state(other)[0], "claimed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
