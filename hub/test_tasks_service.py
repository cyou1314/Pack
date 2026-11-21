#!/usr/bin/env python3
"""Read-only task history service tests using an isolated SQLite fixture."""
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.tasks import get_task_audit, list_task_transfers


class TaskHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-task-history-")
        root = Path(self.temp.name)
        self.context = HubContext(
            base_dir=str(root), db_path=str(root / "history.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(), clock=lambda: 8000.0,
        )
        hub.init(self.context)
        self.conn = self.context.connect()
        self.dispatcher = self.conn.execute(
            "SELECT id FROM members WHERE role='dispatcher'"
        ).fetchone()[0]
        for task_id in (1, 12):
            self.conn.execute(
                "INSERT INTO tasks(id,room_id,title,body,created_by,assignee,status,"
                "created_at,updated_at) VALUES(?,1,?,'',?,?,'open',8000,8000)",
                (task_id, f"task-{task_id}", self.dispatcher, self.dispatcher),
            )
        self.conn.execute(
            "INSERT INTO runs(task_id,member_id,fence,status,outcome,detail,started_at,ended_at)"
            " VALUES(1,?,1,'closed','done','ok',8001,8002)", (self.dispatcher,),
        )
        self.conn.execute(
            "INSERT INTO task_transfers(task_id,from_member,to_member,reason,"
            "actor,created_at,status,accepted_at) "
            "VALUES(1,'dispatcher','worker','handoff','dispatcher',8003,'accepted',8004)"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_transfer_history_orders_by_id_and_rejects_missing_task(self):
        self.conn.execute(
            "INSERT INTO task_transfers(task_id,from_member,to_member,reason,"
            "actor,created_at,status) "
            "VALUES(1,'worker','dispatcher','return','worker',8005,'expired')"
        )
        self.conn.commit()
        output = list_task_transfers(self.context, 1)
        self.assertEqual(output["task_id"], 1)
        self.assertEqual(
            [t["status"] for t in output["transfers"]], ["accepted", "expired"]
        )
        self.assertEqual(output["transfers"][0]["from_member"], "dispatcher")
        with self.assertRaisesRegex(ValueError, "no such task: 999"):
            list_task_transfers(self.context, 999)

    def test_task_audit_matches_only_exact_task_prefix_and_includes_ledger(self):
        for detail in (
            "task 1", "task 1: assigned", "task 1 completed",
            "task 12 ignored", "task 10 ignored", "task 1x ignored",
        ):
            self.conn.execute(
                "INSERT INTO audit(ts,actor,action,detail) VALUES(8000,'tester','task_test',?)",
                (detail,),
            )
        self.conn.commit()
        output = get_task_audit(self.context, 1)
        self.assertEqual(output["task"]["title"], "task-1")
        self.assertEqual(
            [event["detail"] for event in output["events"]],
            ["task 1", "task 1: assigned", "task 1 completed"],
        )
        self.assertFalse(output["events_truncated"])
        self.assertEqual(len(output["runs"]), 1)
        self.assertEqual(output["runs"][0]["fence"], 1)
        self.assertEqual(len(output["transfers"]), 1)
        self.assertEqual(output["transfers"][0]["status"], "accepted")
        with self.assertRaisesRegex(ValueError, "no such task: 999"):
            get_task_audit(self.context, 999)

    def test_task_audit_caps_at_200_events_and_sets_truncated_flag(self):
        self.conn.executemany(
            "INSERT INTO audit(ts,actor,action,detail) "
            "VALUES(8000,'tester','task_test',?)",
            [(f"task 1 event {i:03}",) for i in range(205)],
        )
        self.conn.commit()
        output = get_task_audit(self.context, 1)
        self.assertEqual(len(output["events"]), 200)
        self.assertTrue(output["events_truncated"])
        self.assertEqual(output["events"][0]["detail"], "task 1 event 000")
        self.assertEqual(output["events"][-1]["detail"], "task 1 event 199")


if __name__ == "__main__":
    unittest.main(verbosity=2)
