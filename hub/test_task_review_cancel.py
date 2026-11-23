#!/usr/bin/env python3
"""Isolated tests for dispatcher task_review and task_cancel state transitions."""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.tasks import cancel_task, create_task, review_task


class TaskReviewCancelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-task-review-cancel-")
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
        self.worker_id = self.conn.execute(
            "SELECT id FROM members WHERE name='worker'"
        ).fetchone()[0]

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def new_task(self, status="review", attempts=1, fail_class=None):
        tid = create_task(
            self.context, self.dispatcher, "pack-main",
            "Review task", "Body", "worker",
            is_executor_pinned=hub._is_executor_pinned,
        )["task_id"]
        self.conn.execute(
            "UPDATE tasks SET status=?, attempts=?, fail_class=?, "
            "claim_token='token', lease_expires=10000 WHERE id=?",
            (status, attempts, fail_class, tid),
        )
        self.conn.commit()
        return tid

    def state(self, tid):
        return tuple(self.conn.execute(
            "SELECT status,assignee,claim_token,lease_expires,review_note,"
            "fail_class,next_claim_at,updated_at FROM tasks WHERE id=?",
            (tid,),
        ).fetchone())

    def review(self, tid, approve, note=""):
        return review_task(
            self.context, self.dispatcher, tid, approve, note,
            backoff_seconds=hub._backoff_seconds,
        )

    def cancel(self, tid, close_run=None, desktop_release_for_task=None):
        def default_close(conn, task_id, outcome, actor, detail=""):
            return hub._close_run(
                conn, task_id, outcome, actor, detail, context=self.context
            )
        def default_release(conn, task_id, actor, reason):
            return hub._desktop_release_for_task(
                conn, task_id, actor, reason, context=self.context
            )
        return cancel_task(
            self.context, self.dispatcher, tid,
            close_run=close_run or default_close,
            desktop_release_for_task=desktop_release_for_task or default_release,
        )

    def seed_run_and_lock(self, tid):
        self.conn.execute(
            "INSERT INTO runs(task_id,member_id,fence,claim_token,status,started_at) "
            "VALUES(?,?,1,'token','running',9100)",
            (tid, self.worker_id),
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO desktop_lock(id,holder,task_id,acquired_at) "
            "VALUES(1,'worker',?,9100)", (tid,),
        )
        self.conn.commit()

    def audit_actions(self, tid):
        return [
            (row["action"], row["detail"])
            for row in self.conn.execute(
                "SELECT action,detail FROM audit WHERE detail LIKE ? ORDER BY id",
                (f"task {tid}%",),
            ).fetchall()
        ]

    def test_approve_review_clears_backoff_and_marks_done(self):
        tid = self.new_task(fail_class="quota", attempts=3)
        self.conn.execute("UPDATE tasks SET next_claim_at=10000 WHERE id=?", (tid,))
        self.conn.commit()
        self.assertEqual(self.review(tid, True, "accepted"),
                         {"task_id": tid, "status": "done"})
        state = self.state(tid)
        self.assertEqual(state[0], "done")
        self.assertEqual(state[4:7], ("accepted", None, None))
        self.assertEqual(state[7], 9300.0)
        self.assertIn(
            ("task_review", f"task {tid} approve=True"),
            self.audit_actions(tid),
        )

    def test_reject_review_uses_fallback_and_attempt_backoff(self):
        tid = self.new_task(attempts=3)
        self.assertEqual(self.review(tid, False, "needs fixes"),
                         {"task_id": tid, "status": "open"})
        state = self.state(tid)
        self.assertEqual(state[0], "open")
        self.assertEqual(state[1], self.worker_id)
        self.assertEqual(state[4], "needs fixes")
        self.assertEqual(state[6], 9300.0 + hub._backoff_seconds("unknown", 3))
        self.assertIn(
            ("task_review_backoff",
             f"task {tid} fail_class=unknown next_claim_at={state[6]:.0f}"),
            self.audit_actions(tid),
        )

    def test_reject_review_unassigns_removed_member_and_preserves_failure_class(self):
        tid = self.new_task(attempts=2, fail_class="transient")
        self.conn.execute(
            "UPDATE members SET removed_at=9100 WHERE id=?", (self.worker_id,),
        )
        self.conn.commit()
        self.review(tid, False)
        state = self.state(tid)
        self.assertIsNone(state[1])
        self.assertEqual(state[5], "transient")
        self.assertEqual(state[6], 9300.0 + hub._backoff_seconds("transient", 2))

    def test_review_missing_or_nonreview_rejected(self):
        tid = self.new_task(status="working")
        original = self.state(tid)
        with self.assertRaisesRegex(ValueError, "not in review"):
            self.review(tid, True)
        with self.assertRaisesRegex(ValueError, "no such task"):
            self.review(999999, False)
        self.assertEqual(self.state(tid), original)
        self.assertFalse(any(action == "task_review" for action, _ in self.audit_actions(tid)))

    def test_review_audit_failure_rolls_back_approval_and_rejection(self):
        self.conn.execute(
            "CREATE TRIGGER reject_review_audit BEFORE INSERT ON audit "
            "WHEN NEW.action='task_review' BEGIN "
            "SELECT RAISE(ABORT, 'review audit rejected'); END"
        )
        self.conn.commit()
        for approve in (True, False):
            tid = self.new_task(attempts=2)
            before = self.state(tid)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "review audit rejected"):
                self.review(tid, approve)
            self.assertEqual(self.state(tid), before)
            self.assertFalse(any(
                action in ("task_review", "task_review_backoff")
                for action, _ in self.audit_actions(tid)
            ))

    def test_cancel_active_closes_run_and_releases_desktop(self):
        tid = self.new_task(status="working")
        self.seed_run_and_lock(tid)
        self.assertEqual(self.cancel(tid), {"task_id": tid, "status": "cancelled"})
        state = self.state(tid)
        self.assertEqual((state[0], state[2], state[3], state[7]),
                         ("cancelled", None, None, 9200.0))
        self.assertEqual(tuple(self.conn.execute(
            "SELECT status,outcome,ended_at FROM runs WHERE task_id=?", (tid,),
        ).fetchone()), ("closed", "cancelled", 9200.0))
        self.assertEqual(tuple(self.conn.execute(
            "SELECT holder,task_id FROM desktop_lock WHERE id=1",
        ).fetchone()), (None, None))
        self.assertIn(("task_cancel", f"task {tid}"), self.audit_actions(tid))

    def test_cancel_noop_for_done_and_cancelled_writes_audit(self):
        for status in ("done", "cancelled"):
            tid = self.new_task(status=status)
            before = self.state(tid)
            self.assertEqual(self.cancel(tid), {"task_id": tid, "status": status})
            self.assertEqual(self.state(tid), before)
            self.assertIn(
                ("task_cancel_noop", f"task {tid} already {status}"),
                self.audit_actions(tid),
            )
            self.assertFalse(any(a == "task_cancel" for a, _ in self.audit_actions(tid)))

    def test_cancel_missing_task_is_error(self):
        with self.assertRaisesRegex(ValueError, "no such task"):
            self.cancel(999999)

    def test_cancel_release_failure_rolls_back_task_and_run(self):
        tid = self.new_task(status="claimed")
        self.seed_run_and_lock(tid)
        before = self.state(tid)
        def failed_release(*args):
            raise RuntimeError("release failure")
        with self.assertRaisesRegex(RuntimeError, "release failure"):
            self.cancel(tid, desktop_release_for_task=failed_release)
        self.assertEqual(self.state(tid), before)
        self.assertEqual(self.conn.execute(
            "SELECT status FROM runs WHERE task_id=?", (tid,),
        ).fetchone()[0], "running")
        self.assertEqual(self.conn.execute(
            "SELECT holder FROM desktop_lock WHERE id=1"
        ).fetchone()[0], "worker")
        self.assertNotIn(("task_cancel", f"task {tid}"), self.audit_actions(tid))


if __name__ == "__main__":
    unittest.main(verbosity=2)
