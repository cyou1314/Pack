#!/usr/bin/env python3
"""Isolated task_block regression tests: quota relay and transactional safety."""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.tasks import block_task, claim_task, create_task


class TaskBlockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-task-block-")
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

    def make_claim(self, member=None, title="Draft"):
        member = member or self.worker
        task_id = create_task(
            self.context, self.dispatcher, "pack-main", title, "Body",
            member["name"], is_executor_pinned=hub._is_executor_pinned,
        )["task_id"]

        def release(conn, tid, actor, reason):
            return hub._desktop_release_for_task(
                conn, tid, actor, reason, context=self.context
            )
        token = claim_task(
            self.context, member, task_id, lease_seconds=1800,
            is_executor_pinned=hub._is_executor_pinned,
            desktop_release_for_task=release,
        )["claim_token"]
        return task_id, token

    def block(self, tid, token, reason="failed", fail_class="", member=None,
              auto_relay=None, close_run=None, release=None):
        def actual_close(conn, task_id, outcome, actor, detail=""):
            return hub._close_run(
                conn, task_id, outcome, actor, detail, context=self.context
            )
        def actual_release(conn, task_id, actor, reason):
            return hub._desktop_release_for_task(
                conn, task_id, actor, reason, context=self.context
            )
        def actual_relay(conn, task, from_name, label):
            return hub._auto_relay(
                conn, task, from_name, label, context=self.context
            )
        return block_task(
            self.context, member or self.worker, tid, token, reason, fail_class,
            fail_classes=hub.FAIL_CLASSES,
            backoff_seconds=hub._backoff_seconds,
            close_run=close_run or actual_close,
            desktop_release_for_task=release or actual_release,
            auto_relay=auto_relay or actual_relay,
        )

    def task(self, tid):
        return self.conn.execute(
            "SELECT status,assignee,result,claim_token,lease_expires,"
            "fail_class,next_claim_at,updated_at,attempts "
            "FROM tasks WHERE id=?", (tid,),
        ).fetchone()

    def seed_run_and_lock(self, tid, token):
        self.conn.execute(
            "INSERT INTO runs(task_id,member_id,fence,claim_token,status,started_at) "
            "VALUES(?,?,1,?,'running',9100)",
            (tid, self.worker["id"], token),
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO desktop_lock(id,holder,task_id,acquired_at) "
            "VALUES(1,'worker',?,9100)", (tid,),
        )
        self.conn.commit()

    def test_transient_block_records_backoff_closes_run_and_lock(self):
        tid, token = self.make_claim()
        self.seed_run_and_lock(tid, token)
        outcome = self.block(tid, token, "network error", "transient")
        self.assertEqual(outcome, {
            "task_id": tid, "status": "blocked", "fail_class": "transient",
            "next_claim_at": 9360.0, "relayed_to": None,
            "quota_marked_exhausted": False,
        })
        row = self.task(tid)
        self.assertEqual((row["status"], row["result"], row["claim_token"]),
                         ("blocked", "network error", None))
        self.assertEqual((row["lease_expires"], row["fail_class"],
                          row["next_claim_at"], row["updated_at"]),
                         (None, "transient", 9360.0, 9300.0))
        self.assertEqual(tuple(self.conn.execute(
            "SELECT status,outcome,ended_at FROM runs WHERE task_id=?", (tid,)
        ).fetchone()), ("closed", "blocked", 9200.0))
        self.assertIsNone(self.conn.execute(
            "SELECT holder FROM desktop_lock WHERE id=1"
        ).fetchone()[0])
        self.assertEqual(self.conn.execute(
            "SELECT detail FROM audit WHERE action='task_block'"
        ).fetchone()[0], f"task {tid}: network error fail_class=transient")

    def test_invalid_failure_class_and_attempt_multiplier(self):
        tid, token = self.make_claim()
        self.conn.execute("UPDATE tasks SET attempts=3 WHERE id=?", (tid,))
        self.conn.commit()
        outcome = self.block(tid, token, "unknown", "not-in-list")
        self.assertEqual(outcome["fail_class"], "unknown")
        self.assertEqual(outcome["next_claim_at"],
                         9300.0 + hub._backoff_seconds("unknown", 3))
        self.assertEqual(self.task(tid)["attempts"], 3)

    def test_wrong_holder_missing_task_and_external_cross_room(self):
        tid, token = self.make_claim()
        with self.assertRaisesRegex(ValueError, "claim_token mismatch"):
            self.block(tid, "stale")
        with self.assertRaisesRegex(ValueError, "no such task"):
            self.block(99999, token)
        guest_task, guest_token = self.make_claim(self.guest)
        self.conn.execute(
            "UPDATE tasks SET room_id=(SELECT id FROM rooms WHERE name='other-room') "
            "WHERE id=?", (guest_task,),
        )
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "outside the bound room"):
            self.block(guest_task, guest_token, member=self.guest)
        self.assertEqual(self.task(guest_task)["status"], "claimed")
        self.assertEqual(self.task(tid)["status"], "claimed")

    def test_quota_marks_member_exhausted_without_relay_candidate(self):
        tid, token = self.make_claim()
        outcome = self.block(tid, token, "no tokens", "quota")
        self.assertEqual(outcome["status"], "blocked")
        self.assertEqual(outcome["next_claim_at"], 11100.0)
        self.assertTrue(outcome["quota_marked_exhausted"])
        member = self.conn.execute(
            "SELECT quota_week_used_pct,quota_updated_at,quota_source "
            "FROM members WHERE id=?", (self.worker["id"],),
        ).fetchone()
        self.assertEqual(tuple(member), (100.0, 9300.0, "fact"))
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM audit WHERE action='member_quota_exhausted'"
        ).fetchone()[0], 1)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM task_transfers WHERE task_id=?", (tid,)
        ).fetchone()[0], 0)

    def test_quota_relay_records_pending_transfer_and_message(self):
        tid, token = self.make_claim()
        self.conn.execute(
            "UPDATE members SET last_heartbeat=9300,quota_updated_at=9300,"
            "quota_week_used_pct=10 WHERE id=?", (self.other["id"],),
        )
        self.conn.commit()
        outcome = self.block(tid, token, "quota reached", "quota")
        self.assertEqual((outcome["status"], outcome["relayed_to"]),
                         ("claimed", "other"))
        row = self.task(tid)
        self.assertEqual((row["status"], row["assignee"], row["claim_token"]),
                         ("claimed", self.other["id"], None))
        transfer = self.conn.execute(
            "SELECT from_member,to_member,status FROM task_transfers "
            "WHERE task_id=?", (tid,),
        ).fetchone()
        self.assertEqual(tuple(transfer), ("worker", "other", "pending"))
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE task_id=? AND kind='chat'",
            (tid,),
        ).fetchone()[0], 1)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM audit WHERE action='task_auto_relay'"
        ).fetchone()[0], 1)

    def test_relay_exception_rolls_back_partial_transfer_only(self):
        tid, token = self.make_claim()
        def broken_relay(conn, task, from_name, reason):
            conn.execute(
                "INSERT INTO task_transfers(task_id,from_member,to_member,reason,"
                "actor,created_at,status,expires_at) "
                "VALUES(?,?,?,'partial','auto-relay',9300,'pending',10000)",
                (tid, from_name, "other"),
            )
            conn.execute(
                "UPDATE tasks SET status='claimed',assignee=? WHERE id=?",
                (self.other["id"], tid),
            )
            raise RuntimeError("injected relay failure")
        outcome = self.block(tid, token, "quota reached", "quota",
                             auto_relay=broken_relay)
        self.assertEqual(outcome["status"], "blocked")
        self.assertIsNone(outcome["relayed_to"])
        row = self.task(tid)
        self.assertEqual((row["status"], row["assignee"], row["fail_class"]),
                         ("blocked", self.worker["id"], "quota"))
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM task_transfers WHERE task_id=?", (tid,)
        ).fetchone()[0], 0)
        self.assertEqual(self.conn.execute(
            "SELECT quota_week_used_pct FROM members WHERE id=?",
            (self.worker["id"],),
        ).fetchone()[0], 100.0)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM audit WHERE action='task_auto_relay'"
        ).fetchone()[0], 0)

    def test_run_closure_exception_rolls_back_all_changes(self):
        tid, token = self.make_claim()
        self.seed_run_and_lock(tid, token)
        before = tuple(self.task(tid))
        def fail_close(*args):
            raise RuntimeError("injected run close failure")
        with self.assertRaisesRegex(RuntimeError, "injected run close failure"):
            self.block(tid, token, close_run=fail_close)
        self.assertEqual(tuple(self.task(tid)), before)
        self.assertEqual(self.conn.execute(
            "SELECT status FROM runs WHERE task_id=?", (tid,)
        ).fetchone()[0], "running")
        self.assertEqual(self.conn.execute(
            "SELECT holder FROM desktop_lock WHERE id=1"
        ).fetchone()[0], "worker")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM audit WHERE action='task_block'"
        ).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
