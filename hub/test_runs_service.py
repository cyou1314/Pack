#!/usr/bin/env python3
"""Task run ledger read service tests."""
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.tasks import list_runs


class RunListServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-run-list-")
        root = Path(self.temp.name)
        self.context = HubContext(
            base_dir=str(root), db_path=str(root / "hub.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(), clock=lambda: 12500.0,
        )
        hub.init(self.context)
        self.db = self.context.connect()
        self.dispatcher = dict(self.db.execute(
            "SELECT * FROM members WHERE role='dispatcher'"
        ).fetchone())
        self.db.execute(
            "INSERT INTO members(name,role,token_hash,created_at) "
            "VALUES('alice','member','alice-hash',12000)"
        )
        self.db.commit()
        self.alice = dict(self.db.execute(
            "SELECT * FROM members WHERE name='alice'"
        ).fetchone())
        self.task_a = self.create_task("Task A")
        self.task_b = self.create_task("Task B")
        self.insert_run(self.task_a, 1, "completed", "a1")
        self.insert_run(self.task_b, 1, "failed", "b1")
        self.insert_run(self.task_a, 2, "aborted", "a2")

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def create_task(self, title):
        cursor = self.db.execute(
            "INSERT INTO tasks(room_id,title,body,created_by,assignee,status,"
            "attempts,created_at,updated_at) "
            "VALUES(1,?,? ,? ,?,'done',1,12000,12000)",
            (title, "body", self.dispatcher["id"], self.alice["id"]),
        )
        self.db.commit()
        return cursor.lastrowid

    def insert_run(self, task_id, fence, outcome, detail):
        self.db.execute(
            "INSERT INTO runs(task_id,member_id,fence,status,outcome,detail,"
            "started_at,ended_at) VALUES(?,?,?,'closed',?,?,?,?)",
            (task_id, self.alice["id"], fence, outcome, detail,
             12000 + fence, 12010 + fence),
        )
        self.db.commit()

    def test_lists_all_runs_newest_first_with_member_name(self):
        result = list_runs(self.context)
        self.assertEqual([row["detail"] for row in result["runs"]], [
            "a2", "b1", "a1",
        ])
        self.assertEqual([row["member"] for row in result["runs"]], [
            "alice", "alice", "alice",
        ])
        self.assertEqual(result["runs"][0]["task_id"], self.task_a)

    def test_filters_by_task_id(self):
        result = list_runs(self.context, task_id=self.task_a)
        self.assertEqual([row["detail"] for row in result["runs"]], ["a2", "a1"])
        self.assertTrue(all(row["task_id"] == self.task_a for row in result["runs"]))

    def test_limit_is_passed_through(self):
        result = list_runs(self.context, limit=1)
        self.assertEqual(len(result["runs"]), 1)
        self.assertEqual(result["runs"][0]["detail"], "a2")

    def test_no_matching_task_returns_empty_list(self):
        self.assertEqual(list_runs(self.context, task_id=999999), {"runs": []})


if __name__ == "__main__":
    unittest.main(verbosity=2)
