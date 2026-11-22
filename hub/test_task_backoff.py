#!/usr/bin/env python3
"""Isolated dispatcher tests for task release and manual retry-backoff reset."""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.tasks import clear_task_backoff, create_task, release_task


class TaskBackoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-task-backoff-")
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
            "INSERT INTO members(name,role,token_hash,created_at) "
            "VALUES('worker','member','worker-hash',9200)"
        )
        self.conn.commit()
        self.worker = dict(self.conn.execute(
            "SELECT id,name FROM members WHERE name='worker'"
        ).fetchone())

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def task(self, status="blocked", attempts=1, fail_class="transient"):
        tid = create_task(
            self.context, self.dispatcher, "pack-main", "Retry", "Body",
            "worker", is_executor_pinned=hub._is_executor_pinned,
        )["task_id"]
        self.conn.execute(
            "UPDATE tasks SET status=?, attempts=?, fail_class=?, "
            "claim_token='old-token',lease_expires=9500,"
            "next_claim_at=9550,sched_at=9600 WHERE id=?",
            (status, attempts, fail_class, tid),
        )
        self.conn.commit()
        return tid

    def state(self, tid):
        return tuple(self.conn.execute(
            "SELECT status,assignee,attempts,fail_class,claim_token,"
            "lease_expires,next_claim_at,sched_at,updated_at "
            "FROM tasks WHERE id=?", (tid,),
        ).fetchone())

    def release(self, tid, clear=False):
        return release_task(
            self.context, self.dispatcher, tid, clear,
            max_attempts=hub.MAX_ATTEMPTS,
            backoff_seconds=hub._backoff_seconds,
        )

    def clear(self, tid):
        return clear_task_backoff(self.context, self.dispatcher, tid)

    def test_release_preserves_assignee_and_calculates_backoff(self):
        tid = self.task(attempts=2, fail_class="quota")
        out = self.release(tid)
        self.assertEqual(out, {
            "task_id": tid, "status": "open", "fail_class": "quota",
            "next_claim_at": 9300.0 + hub._backoff_seconds("quota", 2),
        })
        state = self.state(tid)
        self.assertEqual(state[0:4], ("open", self.worker["id"], 2, "quota"))
        self.assertEqual(state[4:7], (None, None, out["next_claim_at"]))
        self.assertEqual(state[8], 9300.0)
        self.assertEqual(self.conn.execute(
            "SELECT detail FROM audit WHERE action='task_release'"
        ).fetchone()[0],
            f"task {tid} fail_class=quota next_claim_at={round(out['next_claim_at'])}"
        )

    def test_release_clear_backoff_override_and_unknown_fallback(self):
        tid = self.task(fail_class=None)
        out = self.release(tid, clear=True)
        self.assertEqual(out["fail_class"], "unknown")
        self.assertIsNone(out["next_claim_at"])
        self.assertEqual(self.state(tid)[6], None)
        self.assertEqual(self.conn.execute(
            "SELECT detail FROM audit WHERE action='task_release'"
        ).fetchone()[0],
            f"task {tid} fail_class=unknown next_claim_at=None"
        )

    def test_release_respects_retry_hard_cap(self):
        tid = self.task(attempts=hub.MAX_ATTEMPTS, fail_class="transient")
        before = self.state(tid)
        with self.assertRaisesRegex(ValueError, "attempts exhausted"):
            self.release(tid)
        self.assertEqual(self.state(tid), before)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM audit WHERE action='task_release'"
        ).fetchone()[0], 0)

    def test_release_unassigns_removed_member(self):
        tid = self.task()
        self.conn.execute(
            "UPDATE members SET removed_at=9100 WHERE id=?",
            (self.worker["id"],),
        )
        self.conn.commit()
        self.release(tid)
        self.assertIsNone(self.state(tid)[1])

    def test_clear_open_resets_scheduler_and_keeps_fail_class(self):
        tid = self.task(status="open", attempts=2, fail_class="task")
        before = self.state(tid)
        out = self.clear(tid)
        self.assertEqual(out, {
            "task_id": tid, "status": "open", "next_claim_at": None,
            "fail_class": "task",
        })
        current = self.state(tid)
        self.assertEqual(current[0:6], before[0:6])
        self.assertEqual(current[6:8], (None, None))
        self.assertEqual(current[8], 9300.0)
        self.assertEqual(self.conn.execute(
            "SELECT detail FROM audit WHERE action='task_clear_backoff'"
        ).fetchone()[0], f"task {tid} fail_class=task")

    def test_clear_blocked_rejected_until_released(self):
        tid = self.task()
        old = self.state(tid)
        with self.assertRaisesRegex(ValueError, "not open"):
            self.clear(tid)
        self.assertEqual(self.state(tid), old)
        self.release(tid)
        self.assertEqual(self.clear(tid)["status"], "open")

    def test_bad_status_and_missing_task_rejected(self):
        tid = self.task(status="review")
        with self.assertRaisesRegex(ValueError, "not blocked"):
            self.release(tid)
        with self.assertRaisesRegex(ValueError, "not open"):
            self.clear(tid)
        with self.assertRaisesRegex(ValueError, "no such task"):
            self.release(999999)
        with self.assertRaisesRegex(ValueError, "no such task"):
            self.clear(999999)

    def test_audit_failure_rolls_back_both_services(self):
        self.conn.execute(
            "CREATE TRIGGER reject_retry_audit BEFORE INSERT ON audit "
            "WHEN NEW.action IN ('task_release','task_clear_backoff') "
            "BEGIN SELECT RAISE(ABORT, 'retry audit failure'); END"
        )
        self.conn.commit()
        for status, command in [
            ("blocked", self.release), ("open", self.clear),
        ]:
            tid = self.task(status=status)
            before = self.state(tid)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "retry audit failure"):
                command(tid)
            self.assertEqual(self.state(tid), before)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM audit WHERE action IN "
            "('task_release','task_clear_backoff')"
        ).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
