#!/usr/bin/env python3
"""Isolated review filing and cross-HubContext concurrency tests."""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.reviews import record_review


class ReviewServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-review-service-")
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
        self.task_id = self.create_pending_approval()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def create_pending_approval(self):
        cursor = self.db.execute(
            "INSERT INTO tasks(room_id,title,body,created_by,status,attempts,"
            "created_at,updated_at) VALUES(1,'Review task','Body',?,'review',1,"
            "12400,12400)",
            (self.dispatcher["id"],),
        )
        task_id = cursor.lastrowid
        self.db.execute(
            "INSERT INTO messages(room_id,author_id,kind,body,task_id,created_at) "
            "VALUES(1,?,'approval_request',?,?,12410)",
            (self.dispatcher["id"], f"申请批准 任务#{task_id}", task_id),
        )
        self.db.commit()
        return task_id

    def record(self, context=None, verdict="allow", task_id=None,
               target_url="https://example.com/path?token=SECRET#frag",
               owner_quote="Owner said proceed", member_desc="Fetched docs"):
        return record_review(
            context or self.context, self.dispatcher,
            self.task_id if task_id is None else task_id,
            target_url, owner_quote, member_desc, verdict,
            approval_state=hub._approval_state,
        )

    def count(self, table, where="", args=()):
        return self.db.execute(
            f"SELECT COUNT(*) FROM {table} {where}", args,
        ).fetchone()[0]

    def test_record_allow_preserves_evidence_and_sanitizes_audit_url(self):
        result = self.record()
        self.assertEqual(result["task_id"], self.task_id)
        self.assertEqual(result["verdict"], "allow")
        evidence = self.db.execute(
            "SELECT target_url,owner_quote,member_desc,verdict,reviewer,created_at "
            "FROM review_log WHERE id=?", (result["review_id"],),
        ).fetchone()
        self.assertEqual(tuple(evidence), (
            "https://example.com/path?token=SECRET#frag",
            "Owner said proceed", "Fetched docs", "allow",
            self.dispatcher["name"], 12500.0,
        ))
        message = self.db.execute(
            "SELECT kind,body,task_id FROM messages WHERE id=?",
            (result["message_id"],),
        ).fetchone()
        self.assertEqual(tuple(message), (
            "decision", f"批准 任务#{self.task_id}（独立审查）", self.task_id,
        ))
        audit = self.db.execute(
            "SELECT actor,action,detail FROM audit WHERE action='review_allow'"
        ).fetchone()
        self.assertEqual(audit["actor"], "reviewer")
        self.assertIn("url=https://example.com/path", audit["detail"])
        self.assertNotIn("SECRET", audit["detail"])
        self.assertNotIn("#frag", audit["detail"])

    def test_record_deny_uses_existing_decision_contract(self):
        result = self.record(verdict="deny")
        self.assertEqual(result["verdict"], "deny")
        self.assertEqual(self.db.execute(
            "SELECT body FROM messages WHERE id=?", (result["message_id"],),
        ).fetchone()[0], f"拒绝 任务#{self.task_id}（独立审查）")
        self.assertEqual(self.count("audit", "WHERE action='review_deny'"), 1)

    def test_rejects_missing_task_and_missing_pending_request(self):
        with self.assertRaisesRegex(ValueError, "no such task"):
            self.record(task_id=999999)
        self.db.execute(
            "DELETE FROM messages WHERE kind='approval_request' AND task_id=?",
            (self.task_id,),
        )
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "no approval request"):
            self.record()
        self.assertEqual(self.count("review_log"), 0)

    def test_rejects_already_decided_approval(self):
        self.db.execute(
            "INSERT INTO messages(room_id,author_id,kind,body,task_id,created_at) "
            "VALUES(1,?,'decision',?,?,12420)",
            (self.dispatcher["id"], f"批准 任务#{self.task_id}", self.task_id),
        )
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "already decided"):
            self.record()
        self.assertEqual(self.count("review_log"), 0)

    def test_validates_verdict_and_all_evidence_before_writing(self):
        for verdict in ("maybe", "", "ALLOW"):
            with self.assertRaisesRegex(ValueError, "verdict must"):
                self.record(verdict=verdict)
        for field in ("target_url", "owner_quote", "member_desc"):
            args = {
                "target_url": "https://example.com",
                "owner_quote": "owner",
                "member_desc": "member",
            }
            args[field] = "  "
            with self.assertRaisesRegex(ValueError, f"missing evidence field: {field}"):
                self.record(**args)
        self.assertEqual(self.count("review_log"), 0)
        self.assertEqual(self.count("messages", "WHERE kind='decision'"), 0)

    def test_audit_failure_rolls_back_review_and_decision_message(self):
        self.db.execute(
            "CREATE TRIGGER reject_review_audit BEFORE INSERT ON audit "
            "WHEN NEW.action='review_allow' BEGIN "
            "SELECT RAISE(ABORT,'review audit rejected'); END"
        )
        self.db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "review audit rejected"):
            self.record()
        self.assertEqual(self.count("review_log"), 0)
        self.assertEqual(self.count("messages", "WHERE kind='decision'"), 0)
        self.assertEqual(self.count("audit", "WHERE action='review_allow'"), 0)

    def test_two_independent_hub_contexts_cannot_double_decide(self):
        root = Path(self.temp.name)
        independent = HubContext(
            base_dir=str(root), db_path=str(root / "hub.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(), clock=lambda: 12500.0,
        )
        # Open once before racing so the test exercises transaction
        # serialization, not simultaneous WAL initialization.
        warm = independent.connect()
        warm.close()
        barrier = threading.Barrier(2)
        results = []
        failures = []

        def submit(context, verdict):
            try:
                barrier.wait(timeout=10)
                results.append(self.record(context=context, verdict=verdict))
            except Exception as error:
                failures.append(error)

        threads = [
            threading.Thread(target=submit, args=(self.context, "allow")),
            threading.Thread(target=submit, args=(independent, "deny")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(len(results), 1)
        self.assertEqual(len(failures), 1)
        self.assertRegex(str(failures[0]), "already decided")
        self.assertEqual(self.count("review_log"), 1)
        self.assertEqual(self.count("messages", "WHERE kind='decision'"), 1)
        self.assertEqual(
            self.count("audit", "WHERE action IN ('review_allow','review_deny')"),
            1,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
