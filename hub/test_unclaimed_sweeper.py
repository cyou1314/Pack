#!/usr/bin/env python3
"""Unclaimed-assignment sweep tests: grace, quota, relay and escalation safety."""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.tasks import inspect_unclaimed_tasks


class UnclaimedSweeperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-unclaimed-sweep-")
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
            "SELECT id,name FROM members WHERE role='dispatcher'",
        ).fetchone())
        for name in ("worker", "receiver", "guest"):
            self.conn.execute(
                "INSERT INTO members(name,role,token_hash,created_at,kind,bound_room_id) "
                "VALUES(?,'member',?,9200,?,?)",
                (name, name+"-hash", "external" if name=="guest" else None,
                 1 if name=="guest" else None),
            )
        self.conn.commit()
        self.worker = self.member("worker")
        self.receiver = self.member("receiver")
        self.guest = self.member("guest")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def member(self, name):
        return dict(self.conn.execute(
            "SELECT id,name,kind FROM members WHERE name=?", (name,),
        ).fetchone())

    def seed_task(self, *, member=None, title="Open work",
                  updated_at=9000.0, sched_at=None, next_claim_at=None,
                  status="open"):
        member = member or self.worker
        cursor = self.conn.execute(
            "INSERT INTO tasks(room_id,title,body,created_by,assignee,status,"
            "attempts,created_at,updated_at,sched_at,next_claim_at) "
            "VALUES(1,?,'Body',?,?,?,0,8900,?,?,?)",
            (title, self.dispatcher["id"], member["id"], status,
             updated_at, sched_at, next_claim_at),
        )
        self.conn.commit()
        return cursor.lastrowid

    def status(self, tid):
        return tuple(self.conn.execute(
            "SELECT status,assignee,claim_token FROM tasks WHERE id=?",
            (tid,),
        ).fetchone())

    def actions(self, action):
        return self.conn.execute(
            "SELECT detail FROM audit WHERE action=? ORDER BY id", (action,),
        ).fetchall()

    def eligible_relay_candidate(self):
        self.conn.execute(
            "UPDATE members SET last_heartbeat=9300,quota_updated_at=9300,"
            "quota_week_used_pct=10 WHERE id=?", (self.receiver["id"],),
        )
        self.conn.commit()

    def inspect(self, relay=None):
        def original(conn, task, from_name, reason):
            return hub._auto_relay(
                conn, task, from_name, reason, context=self.context,
            )
        connection = self.context.connect()
        try:
            inspect_unclaimed_tasks(
                self.context, connection, 9300.0,
                is_executor_pinned=hub._is_executor_pinned,
                auto_relay=relay or original,
                grace_seconds=hub.UNCLAIMED_GRACE,
                external_grace_seconds=hub.UNCLAIMED_GRACE_EXTERNAL,
                heartbeat_stale_seconds=hub.UNCLAIMED_HEARTBEAT_STALE,
                quota_fresh_seconds=hub.RELAY_QUOTA_FRESH,
                quota_headroom=hub.RELAY_QUOTA_HEADROOM,
            )
            connection.commit()
        finally:
            connection.close()

    def test_stale_heartbeat_no_candidate_escalates_once(self):
        tid = self.seed_task()
        self.assertEqual(hub.sweep_once(self.context), 0)
        self.assertEqual(self.status(tid), ("open", self.worker["id"], None))
        detail = self.actions("task_unclaimed_escalate")[0]["detail"]
        self.assertEqual(
            detail, f"task {tid} assignee_id={self.worker['id']} "
                    "assignee=worker cond=A ",
        )
        message = self.conn.execute(
            "SELECT body FROM messages WHERE task_id=? AND kind='chat'",
            (tid,),
        ).fetchone()[0]
        self.assertIn("指派人失联未认领", message)
        self.assertEqual(hub.sweep_once(self.context), 0)
        self.assertEqual(len(self.actions("task_unclaimed_escalate")), 1)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE task_id=? AND kind='chat'",
            (tid,),
        ).fetchone()[0], 1)

    def test_healthy_receiver_can_take_over_without_escalation(self):
        tid = self.seed_task()
        self.eligible_relay_candidate()
        self.inspect()
        self.assertEqual(self.status(tid), ("claimed", self.receiver["id"], None))
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM task_transfers WHERE task_id=? "
            "AND status='pending'", (tid,),
        ).fetchone()[0], 1)
        self.assertEqual(len(self.actions("task_auto_relay")), 1)
        self.assertEqual(len(self.actions("task_unclaimed_escalate")), 0)

    def test_grace_schedule_and_retry_backoff_prevent_alert(self):
        first = self.seed_task(updated_at=9200)
        scheduled = self.seed_task(updated_at=8000, sched_at=9250)
        backoff = self.seed_task(updated_at=8000, next_claim_at=9400)
        self.inspect()
        self.assertEqual(len(self.actions("task_unclaimed_escalate")), 0)
        for tid in (first, scheduled, backoff):
            self.assertEqual(self.status(tid)[0], "open")

    def test_external_assignee_has_longer_grace(self):
        tid = self.seed_task(member=self.guest, updated_at=7000)
        self.inspect()
        self.assertEqual(len(self.actions("task_unclaimed_escalate")), 0)
        self.conn.execute(
            "UPDATE tasks SET updated_at=6000 WHERE id=?", (tid,)
        )
        self.conn.commit()
        self.inspect()
        self.assertEqual(len(self.actions("task_unclaimed_escalate")), 1)
        self.assertIn("assignee=guest", self.actions("task_unclaimed_escalate")[0]["detail"])

    def test_executor_pinned_and_pending_transfer_are_skipped(self):
        pinned = self.seed_task(title="[worker] pinned")
        pending = self.seed_task()
        self.conn.execute(
            "INSERT INTO task_transfers(task_id,from_member,to_member,actor,"
            "created_at,status,expires_at) "
            "VALUES(?,'worker','receiver','worker',9200,'pending',11100)",
            (pending,),
        )
        self.conn.commit()
        self.inspect()
        self.assertEqual(len(self.actions("task_unclaimed_escalate")), 0)
        self.assertEqual(self.status(pinned)[0], "open")
        self.assertEqual(self.status(pending)[0], "open")

    def test_fresh_heartbeat_and_acceptable_quota_do_not_escalate(self):
        tid = self.seed_task()
        self.conn.execute(
            "UPDATE members SET last_heartbeat=9300,"
            "quota_updated_at=9300,quota_week_used_pct=40 "
            "WHERE id=?", (self.worker["id"],),
        )
        self.conn.commit()
        self.inspect()
        self.assertEqual(len(self.actions("task_unclaimed_escalate")), 0)
        self.assertEqual(self.status(tid)[0], "open")

    def test_quota_critical_with_fresh_heartbeat_escalates_b(self):
        tid = self.seed_task()
        self.conn.execute(
            "UPDATE members SET last_heartbeat=9300,"
            "quota_updated_at=9300,quota_week_used_pct=95 "
            "WHERE id=?", (self.worker["id"],),
        )
        self.conn.commit()
        self.inspect()
        self.assertEqual(
            self.actions("task_unclaimed_escalate")[0]["detail"],
            f"task {tid} assignee_id={self.worker['id']} "
            "assignee=worker cond=B ",
        )

    def test_partial_failed_relay_rolled_back_before_escalation(self):
        tid = self.seed_task()
        def corrupt_relay(conn, task, from_name, reason):
            conn.execute(
                "INSERT INTO task_transfers(task_id,from_member,to_member,"
                "actor,created_at,status,expires_at) "
                "VALUES(?,'worker','receiver','auto-relay',9300,'pending',11100)",
                (tid,),
            )
            conn.execute(
                "UPDATE tasks SET status='claimed',assignee=? WHERE id=?",
                (self.receiver["id"], tid),
            )
            raise RuntimeError("simulated relay failure")
        self.inspect(corrupt_relay)
        self.assertEqual(self.status(tid), ("open", self.worker["id"], None))
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM task_transfers WHERE task_id=?", (tid,),
        ).fetchone()[0], 0)
        self.assertEqual(len(self.actions("task_unclaimed_escalate")), 1)

    def test_escalation_audit_failure_rolls_back_message(self):
        tid = self.seed_task()
        self.conn.execute(
            "CREATE TRIGGER block_escalation BEFORE INSERT ON audit "
            "WHEN NEW.action='task_unclaimed_escalate' "
            "BEGIN SELECT RAISE(ABORT, 'escalation failed'); END"
        )
        self.conn.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "escalation failed"):
            self.inspect()
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE task_id=?", (tid,),
        ).fetchone()[0], 0)
        self.assertEqual(len(self.actions("task_unclaimed_escalate")), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
