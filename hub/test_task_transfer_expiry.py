#!/usr/bin/env python3
"""Tests for expiring transfer handoffs and retaining the Mac deployment contract."""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.tasks import (
    accept_task_transfer, claim_task, create_task,
    expire_pending_transfers, transfer_task,
)


class TransferExpiryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-transfer-expiry-")
        root = Path(self.temp.name)
        self.context = HubContext(
            base_dir=str(root), db_path=str(root / "hub.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(), clock=lambda: 9200.0,
        )
        hub.init(self.context)
        self.db = self.context.connect()
        self.dispatcher = dict(self.db.execute(
            "SELECT id,name,role FROM members WHERE role='dispatcher'"
        ).fetchone())
        for name in ("sender", "receiver", "other"):
            self.db.execute(
                "INSERT INTO members(name,role,token_hash,created_at) "
                "VALUES(?,'member',?,9200)", (name, name + "-token")
            )
        self.db.commit()
        self.sender = self.member("sender")
        self.receiver = self.member("receiver")
        self.other = self.member("other")

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def member(self, name):
        return dict(self.db.execute(
            "SELECT id,name,role,kind,bound_room_id FROM members WHERE name=?",
            (name,),
        ).fetchone())

    def handoff(self):
        tid = create_task(
            self.context, self.dispatcher, "pack-main", "Handoff", "Body",
            "sender", is_executor_pinned=hub._is_executor_pinned,
        )["task_id"]

        def release(conn, task_id, actor, reason):
            return hub._desktop_release_for_task(
                conn, task_id, actor, reason, context=self.context
            )
        token = claim_task(
            self.context, self.sender, tid, lease_seconds=1800,
            is_executor_pinned=hub._is_executor_pinned,
            desktop_release_for_task=release,
        )["claim_token"]

        def close_run(conn, task_id, outcome, actor, detail=""):
            return hub._close_run(
                conn, task_id, outcome, actor, detail, context=self.context
            )
        response = transfer_task(
            self.context, self.sender, tid, token, "receiver", "handoff",
            transfer_cap=hub.TRANSFER_CAP,
            transfer_accept_ttl=hub.TRANSFER_ACCEPT_TTL,
            close_run=close_run, desktop_release_for_task=release,
        )
        return tid, response["transfer_id"]

    def state(self, tid):
        return tuple(self.db.execute(
            "SELECT status,assignee,claim_token,lease_expires,attempts,updated_at "
            "FROM tasks WHERE id=?", (tid,),
        ).fetchone())

    def expire(self, transfer_id, at=9000):
        self.db.execute(
            "UPDATE task_transfers SET expires_at=? WHERE id=?",
            (at, transfer_id),
        )
        self.db.commit()

    def transfer(self, transfer_id):
        return self.db.execute(
            "SELECT status,accepted_at FROM task_transfers WHERE id=?",
            (transfer_id,),
        ).fetchone()

    def test_expiry_restores_sender_and_preserves_attempts(self):
        tid, transfer_id = self.handoff()
        self.expire(transfer_id)
        self.assertEqual(hub.sweep_once(self.context), 1)
        self.assertEqual(self.transfer(transfer_id)["status"], "expired")
        self.assertEqual(self.state(tid),
                         ("open", self.sender["id"], None, None, 1, 9200.0))
        self.assertEqual(self.db.execute(
            "SELECT detail FROM audit WHERE action='transfer_expired'"
        ).fetchone()[0],
            f"task {tid} transfer {transfer_id} sender->receiver requeued=True"
        )
        self.assertEqual(hub.sweep_once(self.context), 0)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM audit WHERE action='transfer_expired'"
        ).fetchone()[0], 1)

    def test_unexpired_pending_is_not_requeued(self):
        tid, transfer_id = self.handoff()
        self.assertEqual(hub.sweep_once(self.context), 0)
        self.assertEqual(self.transfer(transfer_id)["status"], "pending")
        self.assertEqual(self.state(tid)[0:3],
                         ("claimed", self.receiver["id"], None))

    def test_removed_sender_requeues_without_assignee(self):
        tid, transfer_id = self.handoff()
        self.expire(transfer_id)
        self.db.execute(
            "UPDATE members SET removed_at=9100 WHERE id=?",
            (self.sender["id"],),
        )
        self.db.commit()
        self.assertEqual(hub.sweep_once(self.context), 1)
        self.assertEqual(self.state(tid)[0:2], ("open", None))

    def test_accepted_transfer_is_not_reexpired_or_unclaimed(self):
        tid, transfer_id = self.handoff()
        accepted = accept_task_transfer(
            self.context, self.receiver, tid, lease_seconds=1800,
        )
        self.expire(transfer_id)
        self.assertEqual(hub.sweep_once(self.context), 0)
        self.assertEqual(self.transfer(transfer_id)["status"], "accepted")
        self.assertEqual(self.state(tid)[0:3],
                         ("claimed", self.receiver["id"], accepted["claim_token"]))

    def test_task_with_new_claim_or_other_status_cannot_be_requeued(self):
        for change, values in [
            ("UPDATE tasks SET claim_token=? WHERE id=?", ("new-token",)),
            ("UPDATE tasks SET status=? WHERE id=?", ("cancelled",)),
            ("UPDATE tasks SET assignee=? WHERE id=?", (self.other["id"],)),
        ]:
            tid, transfer_id = self.handoff()
            self.expire(transfer_id)
            self.db.execute(change, (*values, tid))
            self.db.commit()
            self.assertEqual(hub.sweep_once(self.context), 1)
            self.assertEqual(self.transfer(transfer_id)["status"], "expired")
            state = self.state(tid)
            self.assertEqual(state[0], "cancelled" if values[0] == "cancelled" else "claimed")
            self.assertEqual(state[1],
                             self.other["id"] if values[0] == self.other["id"]
                             else self.receiver["id"])
            self.assertEqual(
                self.db.execute(
                    "SELECT detail FROM audit WHERE action='transfer_expired' "
                    "AND detail LIKE ?", (f"task {tid} %",)
                ).fetchone()[0].endswith("requeued=False"),
                True,
            )

    def test_audit_error_rolls_back_expiry_and_task_requeue(self):
        tid, transfer_id = self.handoff()
        self.expire(transfer_id)
        self.db.execute(
            "CREATE TRIGGER reject_transfer_expiry BEFORE INSERT ON audit "
            "WHEN NEW.action='transfer_expired' BEGIN "
            "SELECT RAISE(ABORT, 'expiry audit rejected'); END"
        )
        self.db.commit()
        connection = self.context.connect()
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "expiry audit rejected"):
                expire_pending_transfers(self.context, connection, 9200.0)
        finally:
            connection.close()
        self.assertEqual(self.transfer(transfer_id)["status"], "pending")
        self.assertEqual(self.state(tid)[0:2],
                         ("claimed", self.receiver["id"]))

    def test_only_expired_rows_counted_and_return_values_unchanged(self):
        first_task, first_transfer = self.handoff()
        second_task, second_transfer = self.handoff()
        self.expire(first_transfer)
        self.assertEqual(hub.sweep_once(self.context), 1)
        self.assertEqual(self.transfer(first_transfer)["status"], "expired")
        self.assertEqual(self.transfer(second_transfer)["status"], "pending")
        self.assertEqual(self.state(second_task)[0], "claimed")
        self.assertEqual(self.state(first_task)[0], "open")


if __name__ == "__main__":
    unittest.main(verbosity=2)
