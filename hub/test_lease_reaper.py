#!/usr/bin/env python3
"""Isolated lease reaper tests for expiry, backoff and safe automatic relay."""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.tasks import reap_expired_leases


class LeaseReaperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-lease-reaper-")
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
        for name in ("worker", "receiver"):
            self.conn.execute(
                "INSERT INTO members(name,role,token_hash,created_at) "
                "VALUES(?,'member',?,9200)", (name, name + "-hash"),
            )
        self.conn.commit()
        self.worker = dict(self.conn.execute(
            "SELECT id,name FROM members WHERE name='worker'",
        ).fetchone())
        self.receiver = dict(self.conn.execute(
            "SELECT id,name FROM members WHERE name='receiver'",
        ).fetchone())

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def seed_task(self, *, attempts=1, status="claimed",
                  lease_expires=9200.0, assignee=None):
        assignee = self.worker["id"] if assignee is None else assignee
        cursor = self.conn.execute(
            "INSERT INTO tasks(room_id,title,body,created_by,assignee,"
            "status,claim_token,lease_expires,attempts,created_at,updated_at) "
            "VALUES(1,'Lease work','Body',?,? ,?,'token',?,?,9100,9100)",
            (self.dispatcher["id"], assignee, status, lease_expires, attempts),
        )
        self.conn.commit()
        return cursor.lastrowid

    def state(self, task_id):
        return self.conn.execute(
            "SELECT status,assignee,claim_token,lease_expires,attempts,"
            "fail_class,next_claim_at,updated_at FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()

    def callbacks(self, auto_relay=None):
        def close(conn, task_id, outcome, actor):
            return hub._close_run(
                conn, task_id, outcome, actor, context=self.context,
            )
        def release(conn, task_id, actor, reason):
            return hub._desktop_release_for_task(
                conn, task_id, actor, reason, context=self.context,
            )
        def actual_relay(conn, task, from_name, reason):
            return hub._auto_relay(
                conn, task, from_name, reason, context=self.context,
            )
        return dict(
            max_attempts=hub.MAX_ATTEMPTS,
            backoff_seconds=hub._backoff_seconds,
            close_run=close,
            desktop_release_for_task=release,
            auto_relay=auto_relay or actual_relay,
        )

    def reap(self, auto_relay=None):
        return hub.sweep_once(self.context) if auto_relay is None else self.direct_reap(auto_relay)

    def direct_reap(self, auto_relay):
        db = self.context.connect()
        try:
            count = reap_expired_leases(
                self.context, db, 9300.0,
                **self.callbacks(auto_relay),
            )
            db.commit()
            return count
        finally:
            db.close()

    def seed_run_and_desktop(self, task_id):
        self.conn.execute(
            "INSERT INTO runs(task_id,member_id,fence,claim_token,status,started_at) "
            "VALUES(?,?,1,'token','running',9100)",
            (task_id, self.worker["id"]),
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO desktop_lock(id,holder,task_id,acquired_at) "
            "VALUES(1,'worker',?,9100)", (task_id,),
        )
        self.conn.commit()

    def test_expired_claim_requeues_with_transient_backoff_and_cleanup(self):
        task_id = self.seed_task(attempts=2)
        self.seed_run_and_desktop(task_id)
        self.assertEqual(self.reap(), 1)
        task = self.state(task_id)
        self.assertEqual(
            (task["status"], task["assignee"], task["claim_token"],
             task["lease_expires"], task["attempts"], task["fail_class"],
             task["next_claim_at"], task["updated_at"]),
            ("open", self.worker["id"], None, None, 2, "transient", 9420.0, 9300.0),
        )
        self.assertEqual(tuple(self.conn.execute(
            "SELECT status,outcome,ended_at FROM runs WHERE task_id=?",
            (task_id,),
        ).fetchone()), ("closed", "reaped", 9200.0))
        self.assertEqual(tuple(self.conn.execute(
            "SELECT holder,task_id FROM desktop_lock WHERE id=1",
        ).fetchone()), (None, None))
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM audit WHERE action='lease_expired_requeue'",
        ).fetchone()[0], 1)

    def test_max_attempts_blocks_and_cleans_run(self):
        task_id = self.seed_task(attempts=hub.MAX_ATTEMPTS)
        self.seed_run_and_desktop(task_id)
        self.assertEqual(self.reap(), 1)
        task = self.state(task_id)
        self.assertEqual(
            (task["status"], task["claim_token"], task["lease_expires"],
             task["fail_class"], task["next_claim_at"]),
            ("blocked", None, None, "transient", None),
        )
        self.assertEqual(self.conn.execute(
            "SELECT outcome FROM runs WHERE task_id=?", (task_id,),
        ).fetchone()[0], "reaped")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM audit WHERE action='task_blocked_max_attempts'",
        ).fetchone()[0], 1)

    def test_removed_assignee_is_cleared_on_requeue(self):
        task_id = self.seed_task()
        self.conn.execute(
            "UPDATE members SET removed_at=9100 WHERE id=?",
            (self.worker["id"],),
        )
        self.conn.commit()
        self.assertEqual(self.reap(), 1)
        self.assertEqual(
            (self.state(task_id)["status"], self.state(task_id)["assignee"]),
            ("open", None),
        )

    def test_unexpired_and_unclaimed_statuses_are_not_reaped(self):
        task_id = self.seed_task(lease_expires=9350.0)
        other_id = self.seed_task(status="review")
        self.assertEqual(self.reap(), 0)
        self.assertEqual(self.state(task_id)["status"], "claimed")
        self.assertEqual(self.state(other_id)["status"], "review")

    def test_successful_relay_creates_one_pending_transfer(self):
        task_id = self.seed_task()
        self.conn.execute(
            "UPDATE members SET last_heartbeat=9300,quota_updated_at=9300,"
            "quota_week_used_pct=10 WHERE id=?", (self.receiver["id"],),
        )
        self.conn.commit()
        self.assertEqual(self.reap(), 1)
        self.assertEqual(
            (self.state(task_id)["status"], self.state(task_id)["assignee"],
             self.state(task_id)["claim_token"]),
            ("claimed", self.receiver["id"], None),
        )
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM task_transfers "
            "WHERE task_id=? AND status='pending'", (task_id,),
        ).fetchone()[0], 1)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM audit WHERE action='task_auto_relay'",
        ).fetchone()[0], 1)

    def test_failed_partial_relay_isolated_by_savepoint(self):
        task_id = self.seed_task()
        def partial_relay(conn, task, from_name, reason):
            conn.execute(
                "INSERT INTO task_transfers(task_id,from_member,to_member,actor,"
                "created_at,status,expires_at) "
                "VALUES(?,?,'receiver','auto-relay',9300,'pending',11100)",
                (task_id, from_name),
            )
            conn.execute(
                "UPDATE tasks SET status='claimed',assignee=? WHERE id=?",
                (self.receiver["id"], task_id),
            )
            raise RuntimeError("relay failed midway")
        self.assertEqual(self.direct_reap(partial_relay), 1)
        self.assertEqual(
            (self.state(task_id)["status"], self.state(task_id)["assignee"]),
            ("open", self.worker["id"]),
        )
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM task_transfers WHERE task_id=?",
            (task_id,),
        ).fetchone()[0], 0)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM audit WHERE action='lease_expired_requeue'",
        ).fetchone()[0], 1)

    def test_close_run_failure_rolls_back_task_and_audit(self):
        task_id = self.seed_task()
        before = tuple(self.state(task_id))
        def broken_close(*_):
            raise RuntimeError("close failed")
        db = self.context.connect()
        try:
            with self.assertRaisesRegex(RuntimeError, "close failed"):
                reap_expired_leases(
                    self.context, db, 9300.0,
                    max_attempts=hub.MAX_ATTEMPTS,
                    backoff_seconds=hub._backoff_seconds,
                    close_run=broken_close,
                    desktop_release_for_task=self.callbacks()["desktop_release_for_task"],
                    auto_relay=self.callbacks()["auto_relay"],
                )
        finally:
            db.close()
        self.assertEqual(tuple(self.state(task_id)), before)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM audit WHERE action='lease_expired_requeue'",
        ).fetchone()[0], 0)

    def test_sweeper_counts_scanned_rows_once(self):
        first = self.seed_task()
        second = self.seed_task(attempts=hub.MAX_ATTEMPTS)
        self.assertEqual(self.reap(), 2)
        self.assertEqual(self.state(first)["status"], "open")
        self.assertEqual(self.state(second)["status"], "blocked")
        self.assertEqual(self.reap(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
