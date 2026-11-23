#!/usr/bin/env python3
"""Desktop lock ownership, audit atomicity and cross-process race tests."""
import hmac
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.desktop import acquire_desktop, desktop_status, release_desktop


class DesktopServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-desktop-service-")
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
        for name in ("alice", "bob"):
            self.db.execute(
                "INSERT INTO members(name,role,token_hash,created_at) "
                "VALUES(?,'member',?,12000)",
                (name, f"{name}-hash"),
            )
        self.db.commit()
        self.alice = dict(self.db.execute(
            "SELECT * FROM members WHERE name='alice'"
        ).fetchone())
        self.bob = dict(self.db.execute(
            "SELECT * FROM members WHERE name='bob'"
        ).fetchone())
        self.task_a = self.create_task(self.alice, "token-a")
        self.task_b = self.create_task(self.bob, "token-b")

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def create_task(self, member, token):
        cursor = self.db.execute(
            "INSERT INTO tasks(room_id,title,body,created_by,assignee,status,"
            "claim_token,lease_expires,attempts,created_at,updated_at) "
            "VALUES(1,'GUI task','body',?,?,'working',?,13000,1,12000,12000)",
            (self.dispatcher["id"], member["id"], token),
        )
        self.db.commit()
        return cursor.lastrowid

    @staticmethod
    def holder(conn, actor, task_id, claim_token):
        task = conn.execute(
            "SELECT * FROM tasks WHERE id=?", (task_id,),
        ).fetchone()
        if not task:
            raise ValueError(f"no such task: {task_id}")
        if (task["assignee"] != actor["id"] or not task["claim_token"]
                or not hmac.compare_digest(task["claim_token"], claim_token or "")):
            raise ValueError("not the current holder (claim_token mismatch)")
        return task

    def acquire(self, context=None, actor=None, task_id=None, token=None):
        return acquire_desktop(
            context or self.context, actor or self.alice,
            self.task_a if task_id is None else task_id,
            "token-a" if token is None else token,
            current_holder=self.holder, snapshot=hub._desktop_snapshot,
        )

    def release(self, context=None, actor=None, task_id=0, token=""):
        return release_desktop(
            context or self.context, actor or self.alice,
            task_id, token,
            current_holder=self.holder, snapshot=hub._desktop_snapshot,
        )

    def count_audit(self, action):
        return self.db.execute(
            "SELECT COUNT(*) FROM audit WHERE action=?", (action,),
        ).fetchone()[0]

    def test_acquire_and_idempotent_reacquire(self):
        first = self.acquire()
        second = self.acquire()
        self.assertEqual(first, {
            "held": True, "task_id": self.task_a, "acquired_at": 12500.0,
        })
        self.assertEqual(second, {
            "held": True, "task_id": self.task_a,
            "acquired_at": 12500.0, "idempotent": True,
        })
        self.assertEqual(self.count_audit("desktop_acquire"), 1)

    def test_busy_error_preserves_existing_holder_payload(self):
        self.acquire()
        with self.assertRaises(ValueError) as error:
            self.acquire(actor=self.bob, task_id=self.task_b, token="token-b")
        payload = json.loads(str(error.exception))
        self.assertEqual(payload, {
            "error": "desktop_busy", "holder": "alice",
            "task_id": self.task_a, "since": 12500.0,
        })

    def test_only_members_can_acquire(self):
        with self.assertRaisesRegex(ValueError, "for members holding a task"):
            self.acquire(actor=self.dispatcher)
        self.assertEqual(self.count_audit("desktop_acquire"), 0)

    def test_acquire_requires_current_claim_token(self):
        with self.assertRaisesRegex(ValueError, "claim_token mismatch"):
            self.acquire(token="wrong")
        self.assertFalse(hub._desktop_snapshot(self.db)["held"])

    def test_member_release_requires_matching_task_and_token(self):
        self.acquire()
        with self.assertRaisesRegex(ValueError, "another task"):
            self.release(actor=self.bob, task_id=self.task_b, token="token-b")
        with self.assertRaisesRegex(ValueError, "claim_token mismatch"):
            self.release(task_id=self.task_a, token="wrong")
        result = self.release(task_id=self.task_a, token="token-a")
        self.assertEqual(result, {
            "held": False, "released": True, "was_task_id": self.task_a,
        })
        self.assertEqual(self.count_audit("desktop_release"), 1)

    def test_dispatcher_can_force_release_without_task_token(self):
        self.acquire()
        result = self.release(actor=self.dispatcher)
        self.assertEqual(result, {
            "held": False, "released": True, "was_task_id": self.task_a,
        })
        self.assertEqual(self.count_audit("desktop_force_release"), 1)

    def test_idle_release_is_idempotent_and_not_audited(self):
        self.assertEqual(self.release(), {"held": False, "released": False})
        self.assertEqual(self.count_audit("desktop_release"), 0)
        self.assertEqual(self.count_audit("desktop_force_release"), 0)

    def test_status_is_public_to_any_authenticated_actor(self):
        self.acquire()
        self.assertEqual(desktop_status(
            self.context, self.bob, snapshot=hub._desktop_snapshot,
        ), {
            "held": True, "holder": "alice",
            "task_id": self.task_a, "since": 12500.0,
        })

    def test_acquire_audit_failure_rolls_back_lock(self):
        self.db.execute(
            "CREATE TRIGGER reject_desktop_acquire BEFORE INSERT ON audit "
            "WHEN NEW.action='desktop_acquire' BEGIN "
            "SELECT RAISE(ABORT,'desktop acquire audit rejected'); END"
        )
        self.db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "desktop acquire audit rejected"):
            self.acquire()
        self.assertFalse(hub._desktop_snapshot(self.db)["held"])

    def test_release_audit_failure_keeps_lock_held(self):
        self.acquire()
        self.db.execute(
            "CREATE TRIGGER reject_desktop_release BEFORE INSERT ON audit "
            "WHEN NEW.action='desktop_release' BEGIN "
            "SELECT RAISE(ABORT,'desktop release audit rejected'); END"
        )
        self.db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "desktop release audit rejected"):
            self.release(task_id=self.task_a, token="token-a")
        self.assertTrue(hub._desktop_snapshot(self.db)["held"])

    def test_independent_contexts_cannot_acquire_same_desktop(self):
        root = Path(self.temp.name)
        independent = HubContext(
            base_dir=str(root), db_path=str(root / "hub.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(), clock=lambda: 12500.0,
        )
        warm = independent.connect()
        warm.close()
        barrier = threading.Barrier(2)
        results, errors = [], []

        def submit(context, actor, task_id, token):
            try:
                barrier.wait(timeout=10)
                results.append(self.acquire(
                    context=context, actor=actor, task_id=task_id, token=token,
                ))
            except Exception as error:
                errors.append(error)

        threads = [
            threading.Thread(target=submit, args=(
                self.context, self.alice, self.task_a, "token-a",
            )),
            threading.Thread(target=submit, args=(
                independent, self.bob, self.task_b, "token-b",
            )),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(json.loads(str(errors[0]))["error"], "desktop_busy")
        state = hub._desktop_snapshot(self.db)
        self.assertTrue(state["held"])
        self.assertIn(state["holder"], ("alice", "bob"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
