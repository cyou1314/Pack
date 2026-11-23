#!/usr/bin/env python3
"""Isolated HubContext tests for task_update service and its security gates."""
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.tasks import claim_task, create_task, update_task


class TaskUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-task-update-")
        root = Path(self.temp.name)
        self.context = HubContext(
            base_dir=str(root), db_path=str(root / "tasks.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(), clock=lambda: 9200.0,
            business_clock=lambda: 9300.0,
        )
        hub.init(self.context)
        self.conn = self.context.connect()
        self.dispatcher = dict(self.conn.execute(
            "SELECT id,name,role FROM members WHERE role='dispatcher'"
        ).fetchone())
        self.conn.execute(
            "INSERT INTO rooms(name,created_at) VALUES('other-room',9200)"
        )
        for name in ("worker", "other"):
            self.conn.execute(
                "INSERT INTO members(name,role,token_hash,created_at) "
                "VALUES(?,'member',?,9200)", (name, name + "-hash")
            )
        self.conn.execute(
            "INSERT INTO members(name,role,token_hash,created_at,kind,bound_room_id)"
            " VALUES('guest','member','guest-hash',9200,'external',1)"
        )
        self.conn.commit()
        self.worker = self.member("worker")
        self.other = self.member("other")
        self.guest = self.member("guest")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def member(self, name):
        return dict(self.conn.execute(
            "SELECT id,name,role,kind,bound_room_id FROM members WHERE name=?",
            (name,),
        ).fetchone())

    def create_and_claim(self, member=None):
        member = member or self.worker
        task_id = create_task(
            self.context, self.dispatcher, "pack-main", "Draft", "Body",
            member["name"], is_executor_pinned=hub._is_executor_pinned,
        )["task_id"]
        def release(conn, task, actor, reason):
            return hub._desktop_release_for_task(
                conn, task, actor, reason, context=self.context
            )
        result = claim_task(
            self.context, member, task_id,
            lease_seconds=1800, is_executor_pinned=hub._is_executor_pinned,
            desktop_release_for_task=release,
        )
        return task_id, result["claim_token"]

    def update(self, task_id, token, note="", member=None, retry_after=None):
        return update_task(
            self.context, member or self.worker, task_id, token, note,
            lease_seconds=1800, external_note_max_chars=4000,
            external_retry_after=retry_after or hub._ext_msg_retry_after,
            external_rate_error=hub._ext_rate_error,
        )

    def task(self, task_id):
        return self.conn.execute(
            "SELECT status,assignee,claim_token,lease_expires,updated_at "
            "FROM tasks WHERE id=?", (task_id,)
        ).fetchone()

    def test_note_writes_report_and_renews_with_wall_clock(self):
        task_id, token = self.create_and_claim()
        result = self.update(task_id, token, "progress")
        self.assertEqual(
            result, {"task_id": task_id, "status": "working", "lease_expires": 11000.0}
        )
        row = self.task(task_id)
        self.assertEqual(tuple(row), ("working", self.worker["id"], token, 11000.0, 9200.0))
        report = self.conn.execute(
            "SELECT room_id,author_id,body,created_at FROM messages "
            "WHERE task_id=? AND kind='report'", (task_id,)
        ).fetchone()
        self.assertEqual(tuple(report), (1, self.worker["id"], "progress", 9200.0))

    def test_no_note_renews_without_creating_message(self):
        task_id, token = self.create_and_claim()
        self.assertEqual(self.update(task_id, token)["status"], "working")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE task_id=? AND kind='report'",
            (task_id,),
        ).fetchone()[0], 0)

    def test_missing_task_wrong_holder_and_stale_token_are_rejected(self):
        task_id, token = self.create_and_claim()
        for actor, candidate, expected in [
            (self.worker, "invalid-token", "claim_token mismatch"),
            (self.other, token, "claim_token mismatch"),
            (self.worker, None, "claim_token mismatch"),
        ]:
            with self.assertRaisesRegex(ValueError, expected):
                self.update(task_id, candidate, "failed", actor)
        with self.assertRaisesRegex(ValueError, "no such task"):
            self.update(99999, token)
        self.assertEqual(self.task(task_id)["status"], "claimed")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE task_id=? AND kind='report'",
            (task_id,),
        ).fetchone()[0], 0)

    def test_external_bound_room_and_note_length(self):
        task_id, token = self.create_and_claim(self.guest)
        self.conn.execute(
            "UPDATE tasks SET room_id=(SELECT id FROM rooms WHERE name='other-room') "
            "WHERE id=?", (task_id,)
        )
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "outside the bound room"):
            self.update(task_id, token, "progress", self.guest)
        self.conn.execute(
            "UPDATE tasks SET room_id=1 WHERE id=?", (task_id,)
        )
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "note too long"):
            self.update(task_id, token, "x" * 4001, self.guest)
        self.assertEqual(self.task(task_id)["status"], "claimed")
        self.assertEqual(
            self.update(task_id, token, "x" * 4000, self.guest)["status"], "working"
        )

    def test_external_quota_denial_audited_without_renew(self):
        task_id, token = self.create_and_claim(self.guest)
        for n in range(5):
            self.conn.execute(
                "INSERT INTO messages(room_id,author_id,kind,body,created_at) "
                "VALUES(1,?,'chat',?,9200)", (self.guest["id"], f"seed-{n}")
            )
        self.conn.commit()
        before = tuple(self.task(task_id))
        with self.assertRaises(ValueError) as raised:
            self.update(task_id, token, "blocked", self.guest)
        payload = json.loads(str(raised.exception))
        self.assertEqual(payload["error"], "rate_limited")
        self.assertGreater(payload["retry_after"], 0)
        self.assertEqual(tuple(self.task(task_id)), before)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM audit WHERE action='external_rate_limited'"
            ).fetchone()[0], 1,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM messages WHERE kind='report' AND author_id=?",
                (self.guest["id"],),
            ).fetchone()[0], 0,
        )
        # The no-note path must not consume a message quota slot.
        self.assertEqual(
            self.update(task_id, token, "", self.guest)["status"], "working"
        )

    def test_report_insert_failure_rolls_back_task_state(self):
        task_id, token = self.create_and_claim()
        old = tuple(self.task(task_id))
        self.conn.execute(
            "CREATE TRIGGER reject_progress BEFORE INSERT ON messages "
            "WHEN NEW.kind='report' BEGIN "
            "SELECT RAISE(ABORT, 'report rejected'); END"
        )
        self.conn.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "report rejected"):
            self.update(task_id, token, "must roll back")
        self.assertEqual(tuple(self.task(task_id)), old)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE task_id=? AND kind='report'",
            (task_id,),
        ).fetchone()[0], 0)

    def test_external_concurrent_reports_cannot_exceed_quota(self):
        task_id, token = self.create_and_claim(self.guest)
        for n in range(4):
            self.conn.execute(
                "INSERT INTO messages(room_id,author_id,kind,body,created_at) "
                "VALUES(1,?,'chat',?,9200)", (self.guest["id"], f"seed-{n}")
            )
        self.conn.commit()
        barrier = threading.Barrier(3)
        successes, failures = [], []
        def attempt():
            barrier.wait(timeout=5)
            try:
                successes.append(self.update(task_id, token, "concurrent", self.guest))
            except ValueError as error:
                failures.append(str(error))
        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertEqual(json.loads(failures[0])["error"], "rate_limited")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE kind='report' AND author_id=?",
            (self.guest["id"],)
        ).fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
