#!/usr/bin/env python3
"""Structured task audit backfill, write path and indexed approval lookup tests."""
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from core.storage import connect
from services.tasks import get_task_audit


class StructuredTaskAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-structured-audit-")
        root = Path(self.temp.name)
        self.context = HubContext(
            base_dir=str(root), db_path=str(root / "hub.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.RLock(), clock=lambda: 5000.0,
        )

        # Simulate a deployed pre-v2 audit table with historical detail-only rows.
        legacy = connect(self.context.db_path)
        legacy.execute(
            "CREATE TABLE audit(id INTEGER PRIMARY KEY, ts REAL NOT NULL, "
            "actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT)"
        )
        details = (
            "task 1",
            "task 1: assigned",
            "task 1 completed",
            "task 12 ignored",
            "task 01 leading zero",
            "task 1x suffix",
            "task 9223372036854775808 overflow",
            "task 9223372036854775807 boundary",
        )
        legacy.executemany(
            "INSERT INTO audit(ts,actor,action,detail) VALUES(1000,'legacy','test',?)",
            [(detail,) for detail in details],
        )
        legacy.commit()
        legacy.close()

        hub.init(self.context)
        self.conn = self.context.connect()
        self.dispatcher = self.conn.execute(
            "SELECT id FROM members WHERE role='dispatcher'"
        ).fetchone()["id"]
        for task_id in (1, 12):
            self.conn.execute(
                "INSERT INTO tasks(id,room_id,title,body,created_by,assignee,status,"
                "created_at,updated_at) VALUES(?,1,?,'',?,NULL,'open',5000,5000)",
                (task_id, f"task-{task_id}", self.dispatcher),
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_legacy_audit_backfill_and_new_writes_preserve_exact_task_ownership(self):
        rows = self.conn.execute(
            "SELECT id,detail,task_id FROM audit WHERE id<=8 ORDER BY id"
        ).fetchall()
        self.assertEqual(
            [(row["detail"], row["task_id"]) for row in rows],
            [
                ("task 1", 1),
                ("task 1: assigned", 1),
                ("task 1 completed", 1),
                ("task 12 ignored", 12),
                ("task 01 leading zero", None),
                ("task 1x suffix", None),
                ("task 9223372036854775808 overflow", None),
                ("task 9223372036854775807 boundary", 9223372036854775807),
            ],
        )

        task_one = get_task_audit(self.context, 1)
        task_twelve = get_task_audit(self.context, 12)
        self.assertEqual(
            [event["detail"] for event in task_one["events"]],
            ["task 1", "task 1: assigned", "task 1 completed"],
        )
        self.assertEqual(
            [event["detail"] for event in task_twelve["events"]],
            ["task 12 ignored"],
        )

        self.context.audit(self.conn, "worker", "task_claim", "task 12 claimed")
        inserted = self.conn.execute(
            "SELECT task_id FROM audit WHERE action='task_claim' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(inserted["task_id"], 12)

        plan = " | ".join(
            row[3] for row in self.conn.execute(
                "EXPLAIN QUERY PLAN SELECT ts,actor,action,detail FROM audit "
                "WHERE task_id=? ORDER BY id ASC LIMIT 201", (1,)
            )
        )
        self.assertIn("idx_audit_task", plan)

    def test_approval_lookup_uses_indexes_and_keeps_null_task_id_fallback(self):
        def add_request(task_id, body, created_at):
            cursor = self.conn.execute(
                "INSERT INTO messages(room_id,author_id,kind,body,task_id,created_at) "
                "VALUES(1,?,'approval_request',?,?,?)",
                (self.dispatcher, body, task_id, created_at),
            )
            return cursor.lastrowid

        request_one_old = add_request(1, "申请批准 任务#1：旧请求", 100)
        request_twelve = add_request(12, "申请批准 任务#12：请求", 200)
        request_one_latest = add_request(1, "申请批准 任务#1：较新请求", 300)
        add_request(77, "申请批准 任务#77：结构化旧请求", 400)
        request_77_legacy = add_request(None, "申请批准 任务#77：未结构化新请求", 500)
        self.conn.execute(
            "INSERT INTO messages(room_id,author_id,kind,body,task_id,created_at) "
            "VALUES(1,?,'decision','批准 任务#1',1,600)",
            (self.dispatcher,),
        )
        self.conn.commit()

        req_one, decision_one = hub._approval_state(self.conn, 1)
        req_twelve, decision_twelve = hub._approval_state(self.conn, 12)
        req_77, decision_77 = hub._approval_state(self.conn, 77)
        self.assertEqual(req_one["id"], request_one_latest)
        self.assertEqual(decision_one, "message")
        self.assertEqual(req_twelve["id"], request_twelve)
        self.assertIsNone(decision_twelve)
        self.assertEqual(req_77["id"], request_77_legacy)
        self.assertIsNone(decision_77)
        self.assertLess(request_one_old, request_one_latest)

        plans = {
            "approval": (
                "SELECT * FROM messages WHERE kind='approval_request' AND task_id=? "
                "ORDER BY id DESC LIMIT 1", (1,), "idx_messages_approval_task"
            ),
            "decision": (
                "SELECT body FROM messages WHERE id>? AND kind IN ('chat','decision') "
                "AND author_id=? ORDER BY id", (0, self.dispatcher),
                "idx_messages_dispatch_decisions"
            ),
            "review": (
                "SELECT id FROM review_log WHERE task_id=? AND created_at>=?",
                (1, 0), "idx_review_log_task_time"
            ),
        }
        for name, (sql, args, expected_index) in plans.items():
            detail = " | ".join(
                row[3] for row in self.conn.execute("EXPLAIN QUERY PLAN " + sql, args)
            )
            self.assertIn(expected_index, detail, f"{name}: {detail}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
