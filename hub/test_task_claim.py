#!/usr/bin/env python3
"""Task claim tests using a disposable SQLite database and HubContext."""
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.tasks import claim_task, create_task


class TaskClaimTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-task-claim-")
        root = Path(self.temp.name)
        self.context = HubContext(
            base_dir=str(root), db_path=str(root / "tasks.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(), clock=lambda: 8200.0,
            business_clock=lambda: 8300.0,
        )
        hub.init(self.context)
        self.conn = self.context.connect()
        self.dispatcher = dict(self.conn.execute(
            "SELECT id,name,role FROM members WHERE role='dispatcher'"
        ).fetchone())
        self.conn.execute(
            "INSERT INTO rooms(name,created_at) VALUES('other-room',8200)"
        )
        for name in ("worker", "other"):
            self.conn.execute(
                "INSERT INTO members(name,role,token_hash,created_at) "
                "VALUES(?,'member',?,8200)", (name, name + "-hash")
            )
        self.conn.execute(
            "INSERT INTO members(name,role,token_hash,created_at,kind,bound_room_id)"
            " VALUES('guest','member','guest-hash',8200,'external',1)"
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

    def make_task(self, title="Task", assignee="", room="pack-main"):
        return create_task(
            self.context, self.dispatcher, room, title, "Body", assignee,
            is_executor_pinned=hub._is_executor_pinned,
        )["task_id"]

    def claim(self, task_id, member=None, release=None):
        if release is None:
            def release(conn, task, actor, reason):
                return hub._desktop_release_for_task(
                    conn, task, actor, reason, context=self.context
                )
        return claim_task(
            self.context, member or self.worker, task_id,
            lease_seconds=1800, is_executor_pinned=hub._is_executor_pinned,
            desktop_release_for_task=release,
        )

    def test_claim_success_clock_token_audit_and_no_double_claim(self):
        task_id = self.make_task()
        result = self.claim(task_id)
        self.assertEqual(result["status"], "claimed")
        self.assertTrue(result["claim_token"])
        self.assertEqual(result["lease_expires"], 10100.0)
        row = self.conn.execute(
            "SELECT status,assignee,attempts,updated_at,lease_expires "
            "FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        self.assertEqual(tuple(row), ("claimed", self.worker["id"], 1, 8300.0, 10100.0))
        record = self.conn.execute(
            "SELECT ts,actor,detail FROM audit WHERE action='task_claim'"
        ).fetchone()
        self.assertEqual(tuple(record), (8200.0, "worker", f"task {task_id}"))
        with self.assertRaisesRegex(ValueError, "status=claimed"):
            self.claim(task_id)

    def test_assignment_backoff_and_missing_task(self):
        task_id = self.make_task(assignee="worker")
        with self.assertRaisesRegex(ValueError, "status=open"):
            self.claim(task_id, self.other)
        self.conn.execute(
            "UPDATE tasks SET next_claim_at=8500 WHERE id=?", (task_id,)
        )
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "retry_after=200s"):
            self.claim(task_id)
        with self.assertRaisesRegex(ValueError, "no such task"):
            self.claim(999999)
        self.assertEqual(self.conn.execute(
            "SELECT attempts FROM tasks WHERE id=?", (task_id,)
        ).fetchone()[0], 0)

    def test_external_bound_room_assignee_and_pinned_guards(self):
        outside = self.make_task(assignee="guest")
        self.conn.execute(
            "UPDATE tasks SET room_id=(SELECT id FROM rooms WHERE name='other-room') "
            "WHERE id=?", (outside,)
        )
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "outside the bound room"):
            self.claim(outside, self.guest)
        unassigned = self.make_task()
        with self.assertRaisesRegex(ValueError, "assigned to themselves"):
            self.claim(unassigned, self.guest)
        pinned = self.make_task(assignee="guest")
        self.conn.execute(
            "UPDATE tasks SET title=? WHERE id=?",
            (hub.EXECUTOR_MARKERS[0] + " fixed", pinned),
        )
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "executor-pinned"):
            self.claim(pinned, self.guest)
        allowed = self.make_task(assignee="guest")
        self.assertEqual(self.claim(allowed, self.guest)["status"], "claimed")

    def test_orphan_desktop_lock_is_released_and_audited(self):
        task_id = self.make_task()
        self.conn.execute(
            "INSERT OR REPLACE INTO desktop_lock(id,holder,task_id,acquired_at) "
            "VALUES(1,'previous',?,8100)", (task_id,)
        )
        self.conn.commit()
        self.claim(task_id)
        self.assertEqual(tuple(self.conn.execute(
            "SELECT holder,task_id,acquired_at FROM desktop_lock WHERE id=1"
        ).fetchone()), (None, None, None))
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM audit WHERE action='desktop_auto_release'"
        ).fetchone()[0], 1)

    def test_desktop_failure_rolls_back_claim_and_audit(self):
        task_id = self.make_task()
        def fail_release(*_args):
            raise RuntimeError("injected failure")
        with self.assertRaisesRegex(RuntimeError, "injected failure"):
            self.claim(task_id, release=fail_release)
        self.assertEqual(tuple(self.conn.execute(
            "SELECT status,attempts,claim_token FROM tasks WHERE id=?",
            (task_id,)
        ).fetchone()), ("open", 0, None))
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM audit WHERE action='task_claim'"
        ).fetchone()[0], 0)

    def test_concurrent_claim_has_one_winner(self):
        task_id = self.make_task()
        barrier = threading.Barrier(3)
        successes, failures = [], []
        def attempt():
            barrier.wait(timeout=5)
            try:
                successes.append(self.claim(task_id))
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
        self.assertIn("status=claimed", failures[0])
        self.assertEqual(self.conn.execute(
            "SELECT attempts FROM tasks WHERE id=?", (task_id,)
        ).fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
