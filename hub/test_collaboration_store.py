import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

from collaboration_store import (
    initialize, write_transaction, create_run, claim_step,
    finish_step, cancel_run, expire_due, snapshot,
)


class CollaborationStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "hub.db")
        self.conn = self.connect()
        self.addCleanup(self.conn.close)
        initialize(self.conn)
        self.eligible = {"a", "b", "c", "d"}

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def create(self, **overrides):
        args = dict(room_id=1, source_message_id=2, deadline=100, now=1,
                    actor_role="owner", mode="team", eligible=self.eligible,
                    leader="a", workers=("b", "c"), reviewer="d")
        args.update(overrides)
        return create_run(self.conn, **args)

    def claim(self, run, key, member, **overrides):
        args = dict(member=member, eligible=self.eligible, now=2)
        args.update(overrides)
        return claim_step(self.conn, run, key, **args)

    def finish(self, run, key, fence, **overrides):
        args = dict(fence=fence, succeeded=True, now=3)
        args.update(overrides)
        return finish_step(self.conn, run, key, **args)

    def test_idempotency_and_conflicts(self):
        with write_transaction(self.conn):
            run = self.create()
            self.assertEqual(run, self.create())
            with self.assertRaises(ValueError):
                self.create(deadline=101)
            with self.assertRaises(ValueError):
                self.create(actor_role="member")
            self.assertNotEqual(run, self.create(room_id=2))
        initialize(self.conn)
        with write_transaction(self.conn):
            self.assertEqual(run, self.create(now=200))
        self.assertEqual(snapshot(self.conn, run)["calls_used"], 0)

    def test_parallel_review_and_final(self):
        with write_transaction(self.conn):
            run = self.create()
            self.assertIsNone(self.claim(run, "review", "d"))
            self.assertIsNone(self.claim(run, "work-0", "c"))
            self.assertIsNone(self.claim(run, "work-0", "b", eligible={"c"}))
            b = self.claim(run, "work-0", "b")
            c = self.claim(run, "work-1", "c")
            self.assertTrue(b and c)
            self.assertIsNone(self.claim(run, "work-0", "b"))
            self.assertFalse(self.finish(run, "work-0", "wrong"))
            self.assertTrue(self.finish(run, "work-0", b))
            self.assertFalse(self.finish(run, "work-0", b))
            self.assertIsNone(self.claim(run, "review", "d"))
            self.assertTrue(self.finish(run, "work-1", c))
            d = self.claim(run, "review", "d")
            self.assertTrue(d)
            self.assertIsNone(self.claim(run, "final", "a"))
            self.assertTrue(self.finish(run, "review", d))
            a = self.claim(run, "final", "a")
            self.assertTrue(a)
            self.assertTrue(self.finish(run, "final", a))
            self.assertFalse(cancel_run(self.conn, run, now=5))
        state = snapshot(self.conn, run)
        self.assertEqual((state["status"], state["calls_used"]), ("succeeded", 4))
        self.assertTrue(all(s["status"] == "succeeded" for s in state["steps"]))
        self.assertNotIn("fence", state["steps"][0])

    def test_failure_stops_other_work(self):
        with write_transaction(self.conn):
            run = self.create()
            b = self.claim(run, "work-0", "b")
            c = self.claim(run, "work-1", "c")
            self.assertTrue(self.finish(run, "work-0", b, succeeded=False))
            self.assertFalse(self.finish(run, "work-1", c))
            self.assertIsNone(self.claim(run, "review", "d"))
        state = snapshot(self.conn, run)
        self.assertEqual((state["status"], state["calls_used"]), ("failed", 2))

    def test_cancel_and_deadline(self):
        with write_transaction(self.conn):
            run = self.create()
            b = self.claim(run, "work-0", "b")
            self.assertTrue(cancel_run(self.conn, run, now=3))
            self.assertFalse(self.finish(run, "work-0", b))
            self.assertIsNone(self.claim(run, "work-1", "c"))
            other = self.create(source_message_id=3)
            c = self.claim(other, "work-1", "c")
            self.assertFalse(self.finish(other, "work-1", c, now=100))
            third = self.create(source_message_id=4)
            self.assertEqual(expire_due(self.conn, now=100), 1)
            self.assertEqual(expire_due(self.conn, now=100), 0)
        self.assertEqual(snapshot(self.conn, run)["status"], "cancelled")
        self.assertEqual(snapshot(self.conn, other)["status"], "expired")
        self.assertEqual(snapshot(self.conn, third)["status"], "expired")

    def test_rollback_and_restart(self):
        with self.assertRaises(RuntimeError):
            self.create()
        with self.assertRaises(ValueError):
            with write_transaction(self.conn):
                run = self.create()
                self.claim(run, "work-0", "b")
                raise ValueError("abort")
        self.assertIsNone(snapshot(self.conn, run))
        with write_transaction(self.conn):
            run = self.create()
            self.assertTrue(self.claim(run, "work-0", "b"))
        reopened = self.connect()
        try:
            with write_transaction(reopened):
                self.assertIsNone(claim_step(reopened, run, "work-0", member="b",
                                             eligible=self.eligible, now=4))
            self.assertEqual(snapshot(reopened, run)["calls_used"], 1)
        finally:
            reopened.close()

    def test_concurrent_claims_only_one_reservation(self):
        with write_transaction(self.conn):
            run = self.create()

        def worker():
            conn = self.connect()
            try:
                with write_transaction(conn):
                    return claim_step(conn, run, "work-0", member="b",
                                      eligible=self.eligible, now=2)
            finally:
                conn.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        self.assertEqual(sum(x is not None for x in results), 1)
        self.assertEqual(snapshot(self.conn, run)["calls_used"], 1)


if __name__ == "__main__":
    unittest.main()
