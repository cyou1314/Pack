#!/usr/bin/env python3
"""Task write-service contract tests in a disposable HubContext database."""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.tasks import create_task, set_task_urgent, schedule_task


class TaskWriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-task-write-")
        root = Path(self.temp.name)
        self.context = HubContext(
            base_dir=str(root), db_path=str(root / "tasks.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(), clock=lambda: 8200.0,
            business_clock=lambda: 8300.0,
        )
        hub.init(self.context)
        self.conn = self.context.connect()
        member = self.conn.execute(
            "SELECT id,name FROM members WHERE role='dispatcher'"
        ).fetchone()
        self.actor = dict(member)
        self.conn.execute(
            "INSERT INTO rooms(name,created_at) VALUES('other-room',8200)"
        )
        self.conn.execute(
            "INSERT INTO members(name,role,token_hash,created_at,kind,bound_room_id)"
            " VALUES('remote','member','test-hash',8200,'external',1)"
        )
        self.conn.execute(
            "INSERT INTO members(name,role,token_hash,created_at,removed_at)"
            " VALUES('removed','member','old-hash',8100,8150)"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def create(self, room="pack-main", title="Draft", body="Body",
               assignee="", urgent=False):
        return create_task(
            self.context, self.actor, room, title, body, assignee, urgent,
            is_executor_pinned=hub._is_executor_pinned,
        )

    def test_create_task_writes_matching_card_audit_and_priority(self):
        result = self.create(title="交付", assignee="remote", urgent=True)
        tid = result["task_id"]
        self.assertEqual(
            result, {"task_id": tid, "status": "open",
                     "assignee": "remote", "urgent": True}
        )
        task = self.conn.execute(
            "SELECT t.*, m.name AS assigned_name FROM tasks t"
            " JOIN members m ON m.id=t.assignee WHERE t.id=?", (tid,)
        ).fetchone()
        self.assertEqual(task["status"], "open")
        self.assertEqual(task["urgent"], 1)
        self.assertEqual(task["assigned_name"], "remote")
        self.assertEqual(task["created_by"], self.actor["id"])
        self.assertEqual(task["created_at"], 8200.0)
        message = self.conn.execute(
            "SELECT kind,body,task_id,created_at FROM messages WHERE task_id=?",
            (tid,),
        ).fetchone()
        self.assertEqual(
            tuple(message), ("task_card", f"任务#{tid} 交付", tid, 8200.0)
        )
        audit = self.conn.execute(
            "SELECT actor,action,detail FROM audit WHERE action='task_create'"
        ).fetchone()
        self.assertEqual(
            tuple(audit), ("dispatcher", "task_create", f"task {tid}: 交付 [urgent]")
        )

    def test_create_task_rejects_invalid_members_and_external_cross_room(self):
        for room, assignee, title, body, expected in [
            ("absent", "", "Draft", "Body", "no such room"),
            ("pack-main", "absent", "Draft", "Body", "no such member"),
            ("pack-main", "removed", "Draft", "Body", "member is removed"),
            ("other-room", "remote", "Draft", "Body", "permanent:"),
            ("pack-main", "remote", "fixed", "Body", "permanent:"),
        ]:
            pinned = title == "fixed"
            if pinned:
                title = hub.EXECUTOR_MARKERS[0] + " fixed"
            with self.assertRaisesRegex(ValueError, expected):
                self.create(room=room, title=title, body=body, assignee=assignee)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM messages WHERE kind='task_card'"
            ).fetchone()[0], 0,
        )

    def test_create_task_rolls_back_when_card_insert_fails(self):
        self.conn.execute(
            "CREATE TRIGGER fail_task_card BEFORE INSERT ON messages "
            "WHEN NEW.kind='task_card' BEGIN SELECT RAISE(ABORT, 'card rejected'); END"
        )
        self.conn.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "card rejected"):
            self.create()
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM audit WHERE action='task_create'"
            ).fetchone()[0], 0,
        )

    def test_urgent_set_idempotent_audited_and_business_clock_based(self):
        tid = self.create()["task_id"]
        first = set_task_urgent(self.context, self.actor, tid, True)
        repeated = set_task_urgent(self.context, self.actor, tid, True)
        self.assertEqual(first, {"task_id": tid, "urgent": True})
        self.assertEqual(repeated, first)
        task = self.conn.execute(
            "SELECT urgent,updated_at FROM tasks WHERE id=?", (tid,)
        ).fetchone()
        self.assertEqual(tuple(task), (1, 8300.0))
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM audit WHERE action='task_set_urgent'"
            ).fetchone()[0], 2,
        )

    def test_urgent_rejects_missing_done_and_cancelled_tasks(self):
        with self.assertRaisesRegex(ValueError, "no such task"):
            set_task_urgent(self.context, self.actor, 1000, True)
        for status in ("done", "cancelled"):
            tid = self.create(title=status)["task_id"]
            self.conn.execute(
                "UPDATE tasks SET status=? WHERE id=?", (status, tid)
            )
            self.conn.commit()
            with self.assertRaisesRegex(ValueError, "cannot set urgent"):
                set_task_urgent(self.context, self.actor, tid, True)
            self.assertEqual(
                self.conn.execute(
                    "SELECT urgent FROM tasks WHERE id=?", (tid,)
                ).fetchone()[0], 0,
            )

    def schedule(self, room="pack-main", member_name="remote",
                 title="Scheduled", body="Body", at_epoch=8700):
        return schedule_task(
            self.context, self.actor, room, title, body, member_name,
            at_epoch, is_executor_pinned=hub._is_executor_pinned,
        )

    def test_schedule_creates_deferred_task_card_and_audit(self):
        result = self.schedule()
        tid = result["task_id"]
        self.assertEqual(
            result, {"task_id": tid, "status": "open",
                     "assignee": "remote", "scheduled_at": 8700.0}
        )
        task = self.conn.execute(
            "SELECT status,next_claim_at,sched_at,created_at,updated_at"
            " FROM tasks WHERE id=?", (tid,),
        ).fetchone()
        self.assertEqual(tuple(task), ("open", 8700.0, 8700.0, 8300.0, 8300.0))
        self.assertEqual(
            self.conn.execute(
                "SELECT kind,body FROM messages WHERE task_id=?", (tid,)
            ).fetchone()["kind"], "task_card",
        )
        audit = self.conn.execute(
            "SELECT actor,detail FROM audit WHERE action='task_schedule'"
        ).fetchone()
        self.assertEqual(tuple(audit), ("dispatcher", f"task {tid} -> remote at 8700"))

    def test_schedule_rejects_invalid_dates_members_and_room_binding(self):
        for room, member, title, at, expected in [
            ("pack-main", "remote", "Scheduled", 8300, "future"),
            ("missing-room", "remote", "Scheduled", 8700, "no such room"),
            ("pack-main", "missing", "Scheduled", 8700, "no such member"),
            ("pack-main", "removed", "Scheduled", 8700, "member is removed"),
            ("pack-main", "dispatcher", "Scheduled", 8700, "not an AI member"),
            ("other-room", "remote", "Scheduled", 8700, "permanent:"),
            ("pack-main", "remote", hub.EXECUTOR_MARKERS[0] + " fixed",
             8700, "permanent:"),
        ]:
            with self.assertRaisesRegex(ValueError, expected):
                self.schedule(
                    room=room, member_name=member, title=title, at_epoch=at
                )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0
        )

    def test_schedule_rolls_back_task_when_card_insert_fails(self):
        self.conn.execute(
            "CREATE TRIGGER fail_scheduled_card BEFORE INSERT ON messages "
            "WHEN NEW.kind='task_card' BEGIN "
            "SELECT RAISE(ABORT, 'schedule card rejected'); END"
        )
        self.conn.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "schedule card rejected"):
            self.schedule()
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM audit WHERE action='task_schedule'"
            ).fetchone()[0], 0,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
