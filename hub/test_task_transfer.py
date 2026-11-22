#!/usr/bin/env python3
"""Isolation and transaction tests for task transfer and acceptance services."""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.tasks import (
    accept_task_transfer, claim_task, create_task, transfer_task,
)


class TaskTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-task-transfer-")
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
        for name in ("worker", "receiver", "other", "removed", "owner"):
            self.conn.execute(
                "INSERT INTO members(name,role,token_hash,created_at) "
                "VALUES(?,'member',?,9200)", (name, f"{name}-hash"),
            )
        self.conn.execute(
            "INSERT INTO members(name,role,token_hash,created_at,kind,bound_room_id) "
            "VALUES('guest','member','guest-hash',9200,'external',1)"
        )
        self.conn.execute(
            "UPDATE members SET removed_at=9100 WHERE name='removed'"
        )
        self.conn.commit()
        self.worker = self.member("worker")
        self.receiver = self.member("receiver")
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
        tid = create_task(
            self.context, self.dispatcher, "pack-main", "Handoff", "Body",
            member["name"], is_executor_pinned=hub._is_executor_pinned,
        )["task_id"]
        def release(conn, task_id, actor, reason):
            return hub._desktop_release_for_task(
                conn, task_id, actor, reason, context=self.context,
            )
        token = claim_task(
            self.context, member, tid, lease_seconds=1800,
            is_executor_pinned=hub._is_executor_pinned,
            desktop_release_for_task=release,
        )["claim_token"]
        return tid, token

    def transfer(self, tid, token, to="receiver", reason="handoff",
                 member=None, close_run=None, release=None):
        def actual_close(conn, task_id, outcome, actor, detail=""):
            return hub._close_run(
                conn, task_id, outcome, actor, detail, context=self.context,
            )
        def actual_release(conn, task_id, actor, why):
            return hub._desktop_release_for_task(
                conn, task_id, actor, why, context=self.context,
            )
        return transfer_task(
            self.context, member or self.worker, tid, token, to, reason,
            transfer_cap=hub.TRANSFER_CAP,
            transfer_accept_ttl=hub.TRANSFER_ACCEPT_TTL,
            close_run=close_run or actual_close,
            desktop_release_for_task=release or actual_release,
        )

    def accept(self, tid, member=None):
        return accept_task_transfer(
            self.context, member or self.receiver, tid,
            lease_seconds=1800,
        )

    def task(self, tid):
        return self.conn.execute(
            "SELECT status,assignee,claim_token,lease_expires,"
            "next_claim_at,attempts,updated_at FROM tasks WHERE id=?",
            (tid,),
        ).fetchone()

    def transfer_rows(self, tid):
        return self.conn.execute(
            "SELECT id,from_member,to_member,status,accepted_at,expires_at "
            "FROM task_transfers WHERE task_id=? ORDER BY id", (tid,),
        ).fetchall()

    def seed_running(self, tid, token):
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

    def test_transfer_closes_run_clears_token_and_releases_desktop(self):
        tid, token = self.make_claim()
        self.seed_running(tid, token)
        out = self.transfer(tid, token, reason="capacity")
        row = self.task(tid)
        self.assertEqual(out["transfer_status"], "pending")
        self.assertEqual(out["from_member"], "worker")
        self.assertEqual(out["to_member"], "receiver")
        self.assertFalse(out["forced"])
        self.assertEqual(out["attempts"], 1)
        self.assertEqual((row["status"], row["assignee"], row["claim_token"],
                          row["lease_expires"], row["attempts"], row["updated_at"]),
                         ("claimed", self.receiver["id"], None, None, 1, 9200.0))
        self.assertEqual(tuple(self.conn.execute(
            "SELECT status,outcome FROM runs WHERE task_id=?", (tid,)
        ).fetchone()), ("closed", "transferred"))
        self.assertEqual(tuple(self.conn.execute(
            "SELECT holder,task_id FROM desktop_lock WHERE id=1",
        ).fetchone()), (None, None))
        chain = self.transfer_rows(tid)
        self.assertEqual(len(chain), 1)
        self.assertEqual((chain[0]["status"], chain[0]["expires_at"]),
                         ("pending", out["expires_at"]))
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE task_id=? AND kind='chat'",
            (tid,),
        ).fetchone()[0], 1)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM audit WHERE action='task_transfer'"
        ).fetchone()[0], 1)
        with self.assertRaisesRegex(ValueError, "claim_token mismatch"):
            self.transfer(tid, token, "other")

    def test_accept_issues_new_token_and_preserves_attempts(self):
        tid, old_token = self.make_claim()
        x = self.transfer(tid, old_token)
        accepted = self.accept(tid)
        self.assertEqual(accepted["transfer_id"], x["transfer_id"])
        self.assertEqual(accepted["lease_expires"], 11000.0)
        self.assertNotEqual(accepted["claim_token"], old_token)
        self.assertEqual(accepted["status"], "claimed")
        row = self.task(tid)
        self.assertEqual((row["assignee"], row["claim_token"],
                          row["lease_expires"], row["attempts"]),
                         (self.receiver["id"], accepted["claim_token"], 11000.0, 1))
        self.assertEqual(self.transfer_rows(tid)[0]["status"], "accepted")
        self.assertEqual(self.transfer_rows(tid)[0]["accepted_at"], 9200.0)
        with self.assertRaisesRegex(ValueError, "already accepted"):
            self.accept(tid)

    def test_non_holder_rejected_dispatcher_can_force(self):
        tid, token = self.make_claim()
        with self.assertRaisesRegex(ValueError, "claim_token mismatch"):
            self.transfer(tid, token, member=self.other)
        with self.assertRaisesRegex(ValueError, "claim_token mismatch"):
            self.transfer(tid, "invalid")
        out = self.transfer(tid, "", member=self.dispatcher, reason="override")
        self.assertTrue(out["forced"])
        self.assertEqual(self.task(tid)["assignee"], self.receiver["id"])
        audit = self.conn.execute(
            "SELECT detail FROM audit WHERE action='task_transfer'"
        ).fetchone()[0]
        self.assertIn("(forced)", audit)

    def test_invalid_target_and_pending_handoff_rejected(self):
        tid, token = self.make_claim()
        for target, pattern in [
            ("missing", "no such member"),
            ("removed", "member is removed"),
            ("owner", "owner is not an executor"),
            ("guest", "external member"),
            ("worker", "current holder"),
            (self.dispatcher["name"], "transfer target must be a member"),
        ]:
            with self.assertRaisesRegex(ValueError, pattern):
                self.transfer(tid, token, to=target)
        self.transfer(tid, token)
        with self.assertRaisesRegex(ValueError, "pending transfer"):
            self.transfer(tid, "", member=self.dispatcher, to="other")

    def test_relay_cap_commits_rejection_audit_and_room_notice(self):
        tid, token = self.make_claim()
        for i in range(hub.TRANSFER_CAP):
            self.conn.execute(
                "INSERT INTO task_transfers(task_id,from_member,to_member,actor,"
                "created_at,status,expires_at) VALUES(?,?,'other','seed',9100,'expired',9150)",
                (tid, "worker"),
            )
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "relay cap reached"):
            self.transfer(tid, token)
        self.assertEqual(self.task(tid)["assignee"], self.worker["id"])
        self.assertEqual(len(self.transfer_rows(tid)), hub.TRANSFER_CAP)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM audit WHERE action='transfer_cap_reject'"
        ).fetchone()[0], 1)
        message = self.conn.execute(
            "SELECT body FROM messages WHERE task_id=? AND kind='chat'",
            (tid,),
        ).fetchone()[0]
        self.assertIn("接力上限", message)

    def test_expired_wrong_recipient_and_changed_task_reject_accept(self):
        tid, token = self.make_claim()
        with self.assertRaisesRegex(ValueError, "has no transfer"):
            self.accept(tid)
        self.transfer(tid, token)
        with self.assertRaisesRegex(ValueError, "addressed to"):
            self.accept(tid, self.other)
        self.conn.execute(
            "UPDATE task_transfers SET expires_at=9000 WHERE task_id=?", (tid,)
        )
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "sweeper will requeue"):
            self.accept(tid)
        self.conn.execute(
            "UPDATE task_transfers SET expires_at=10000 WHERE task_id=?", (tid,)
        )
        self.conn.execute(
            "UPDATE tasks SET status='cancelled' WHERE id=?", (tid,)
        )
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "not acceptable"):
            self.accept(tid)
        self.assertEqual(self.transfer_rows(tid)[0]["status"], "pending")

    def test_transfer_failure_rolls_back_run_and_ownership(self):
        tid, token = self.make_claim()
        self.seed_running(tid, token)
        original = tuple(self.task(tid))
        def bad_release(*args):
            raise RuntimeError("desktop release failed")
        with self.assertRaisesRegex(RuntimeError, "desktop release failed"):
            self.transfer(tid, token, release=bad_release)
        self.assertEqual(tuple(self.task(tid)), original)
        self.assertEqual(len(self.transfer_rows(tid)), 0)
        self.assertEqual(self.conn.execute(
            "SELECT status FROM runs WHERE task_id=?", (tid,)
        ).fetchone()[0], "running")
        self.assertEqual(self.conn.execute(
            "SELECT holder FROM desktop_lock WHERE id=1"
        ).fetchone()[0], "worker")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM audit WHERE action='task_transfer'"
        ).fetchone()[0], 0)

    def test_accept_audit_error_rolls_back_ledger_and_claim(self):
        tid, token = self.make_claim()
        self.transfer(tid, token)
        self.conn.execute(
            "CREATE TRIGGER reject_accept BEFORE INSERT ON audit "
            "WHEN NEW.action='task_accept_transfer' "
            "BEGIN SELECT RAISE(ABORT, 'audit rejected'); END"
        )
        self.conn.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "audit rejected"):
            self.accept(tid)
        self.assertEqual(self.transfer_rows(tid)[0]["status"], "pending")
        self.assertIsNone(self.task(tid)["claim_token"])

    def test_concurrent_accept_one_winner(self):
        tid, token = self.make_claim()
        self.transfer(tid, token)
        start = threading.Barrier(3)
        successes, errors = [], []
        def attempt():
            start.wait(timeout=5)
            try:
                successes.append(self.accept(tid))
            except ValueError as exc:
                errors.append(str(exc))
        workers = [threading.Thread(target=attempt) for _ in range(2)]
        for worker in workers:
            worker.start()
        start.wait(timeout=5)
        for worker in workers:
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("already accepted", errors[0])
        self.assertEqual(self.transfer_rows(tid)[0]["status"], "accepted")
        self.assertEqual(self.task(tid)["claim_token"], successes[0]["claim_token"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
