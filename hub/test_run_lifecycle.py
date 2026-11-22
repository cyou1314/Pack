#!/usr/bin/env python3
"""Isolated regression tests for Run lifecycle, fences and transactional audit."""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.tasks import claim_task, create_task, finish_run, start_run


class RunLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-run-lifecycle-")
        root = Path(self.temp.name)
        self.context = HubContext(
            base_dir=str(root), db_path=str(root / "hub.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(), clock=lambda: 9200.0,
            business_clock=lambda: 9300.0,
        )
        hub.init(self.context)
        self.conn = self.context.connect()
        self.dispatcher = dict(self.conn.execute(
            "SELECT id,name,role FROM members WHERE role='dispatcher'"
        ).fetchone())
        for name in ("worker", "other"):
            self.conn.execute(
                "INSERT INTO members(name,role,token_hash,created_at) "
                "VALUES(?,'member',?,9200)", (name, name + "-hash"),
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

    def make_claim(self, member=None):
        member = member or self.worker
        task_id = create_task(
            self.context, self.dispatcher, "pack-main", "Run lifecycle", "Body",
            member["name"], is_executor_pinned=hub._is_executor_pinned,
        )["task_id"]
        def release(conn, tid, actor, reason):
            return hub._desktop_release_for_task(
                conn, tid, actor, reason, context=self.context,
            )
        token = claim_task(
            self.context, member, task_id, lease_seconds=1800,
            is_executor_pinned=hub._is_executor_pinned,
            desktop_release_for_task=release,
        )["claim_token"]
        return task_id, token

    def start(self, task_id, token, member=None):
        return start_run(
            self.context, member or self.worker, task_id, token,
        )

    def finish(self, task_id, token, outcome="completed", detail="",
               member=None, close_run=None):
        def default_close(conn, task_id, outcome, actor, detail=""):
            return hub._close_run(
                conn, task_id, outcome, actor, detail, context=self.context,
            )
        return finish_run(
            self.context, member or self.worker, task_id, token,
            outcome, detail, close_run=close_run or default_close,
        )

    def runs(self, task_id):
        return self.conn.execute(
            "SELECT * FROM runs WHERE task_id=? ORDER BY fence",
            (task_id,),
        ).fetchall()

    def audit_count(self, action):
        return self.conn.execute(
            "SELECT COUNT(*) FROM audit WHERE action=?", (action,),
        ).fetchone()[0]

    def test_start_then_finish_retains_task_and_increments_fence(self):
        tid, token = self.make_claim()
        started = self.start(tid, token)
        self.assertEqual(started, {
            "run_id": started["run_id"], "task_id": tid, "fence": 1,
            "status": "running", "started_at": 9200.0,
        })
        self.assertEqual(self.runs(tid)[0]["claim_token"], token)
        closed = self.finish(tid, token, "completed", "done")
        self.assertEqual(closed, {
            "run_id": started["run_id"], "task_id": tid,
            "fence": 1, "status": "closed", "outcome": "completed",
        })
        self.assertEqual(self.runs(tid)[0]["ended_at"], 9200.0)
        self.assertEqual(self.runs(tid)[0]["detail"], None)
        self.assertEqual(self.conn.execute(
            "SELECT status FROM tasks WHERE id=?", (tid,),
        ).fetchone()[0], "claimed")
        next_run = self.start(tid, token)
        self.assertEqual(next_run["fence"], 2)
        self.assertNotEqual(next_run["run_id"], started["run_id"])
        self.assertEqual(self.audit_count("run_start"), 2)
        self.assertEqual(self.audit_count("run_close"), 1)
        audit = self.conn.execute(
            "SELECT detail FROM audit WHERE action='run_close'"
        ).fetchone()[0]
        self.assertIn("outcome=completed done", audit)

    def test_duplicate_start_is_rejected_and_audited(self):
        tid, token = self.make_claim()
        started = self.start(tid, token)
        with self.assertRaisesRegex(ValueError, "run already running"):
            self.start(tid, token)
        self.assertEqual(len(self.runs(tid)), 1)
        self.assertEqual(self.audit_count("run_fence_reject"), 1)
        self.assertIn(
            f"run {started['run_id']} fence=1",
            self.conn.execute(
                "SELECT detail FROM audit WHERE action='run_fence_reject'"
            ).fetchone()[0],
        )

    def test_outcome_variants_and_missing_running_run(self):
        tid, token = self.make_claim()
        with self.assertRaisesRegex(ValueError, "no running run"):
            self.finish(tid, token)
        with self.assertRaisesRegex(ValueError, "outcome must be"):
            self.finish(tid, token, "unknown")
        for outcome in ("failed", "aborted"):
            started = self.start(tid, token)
            finished = self.finish(tid, token, outcome)
            self.assertEqual(finished["outcome"], outcome)
            self.assertEqual(self.runs(tid)[-1]["fence"], started["fence"])
            self.assertEqual(self.runs(tid)[-1]["outcome"], outcome)

    def test_wrong_holder_missing_task_and_task_state_restrictions(self):
        tid, token = self.make_claim()
        with self.assertRaisesRegex(ValueError, "claim_token mismatch"):
            self.start(tid, "wrong")
        with self.assertRaisesRegex(ValueError, "claim_token mismatch"):
            self.start(tid, token, self.other)
        with self.assertRaisesRegex(ValueError, "no such task"):
            self.start(999999, token)
        self.conn.execute(
            "UPDATE tasks SET status='review' WHERE id=?", (tid,),
        )
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "not in claimed/working"):
            self.start(tid, token)
        self.assertEqual(len(self.runs(tid)), 0)

    def test_external_cross_room_is_denied_for_start_and_finish(self):
        tid, token = self.make_claim(self.guest)
        self.conn.execute(
            "UPDATE tasks SET room_id=("
            "SELECT id FROM rooms WHERE name='other-room') WHERE id=?", (tid,),
        )
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "outside the bound room"):
            self.start(tid, token, self.guest)
        with self.assertRaisesRegex(ValueError, "outside the bound room"):
            self.finish(tid, token, member=self.guest)
        self.assertEqual(len(self.runs(tid)), 0)

    def test_start_audit_failure_rolls_back_run_row(self):
        tid, token = self.make_claim()
        self.conn.execute(
            "CREATE TRIGGER reject_run_start BEFORE INSERT ON audit "
            "WHEN NEW.action='run_start' BEGIN "
            "SELECT RAISE(ABORT, 'start audit failed'); END"
        )
        self.conn.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "start audit failed"):
            self.start(tid, token)
        self.assertEqual(len(self.runs(tid)), 0)
        self.assertEqual(self.audit_count("run_start"), 0)

    def test_finish_audit_failure_rolls_back_run_closure(self):
        tid, token = self.make_claim()
        self.start(tid, token)
        self.conn.execute(
            "CREATE TRIGGER reject_run_close BEFORE INSERT ON audit "
            "WHEN NEW.action='run_close' BEGIN "
            "SELECT RAISE(ABORT, 'close audit failed'); END"
        )
        self.conn.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "close audit failed"):
            self.finish(tid, token)
        self.assertEqual(self.runs(tid)[0]["status"], "running")
        self.assertIsNone(self.runs(tid)[0]["ended_at"])
        self.assertEqual(self.audit_count("run_close"), 0)

    def test_duplicate_start_concurrency_one_winner(self):
        tid, token = self.make_claim()
        barrier = threading.Barrier(3)
        wins, failures = [], []
        def attempt():
            barrier.wait(timeout=5)
            try:
                wins.append(self.start(tid, token))
            except ValueError as error:
                failures.append(str(error))
        workers = [threading.Thread(target=attempt) for _ in range(2)]
        for worker in workers:
            worker.start()
        barrier.wait(timeout=5)
        for worker in workers:
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
        self.assertEqual(len(wins), 1)
        self.assertEqual(len(failures), 1)
        self.assertIn("run already running", failures[0])
        self.assertEqual(self.audit_count("run_fence_reject"), 1)
        self.assertEqual(len(self.runs(tid)), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
