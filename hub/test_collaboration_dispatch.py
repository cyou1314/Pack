import sqlite3
import unittest

from collaboration_store import initialize, write_transaction, create_run, snapshot
from collaboration_dispatch import (
    ready_steps, bind_task, linked_task, reserve_claim, reconcile_terminal,
)


class CollaborationDispatchTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript("""
            CREATE TABLE members(id INTEGER PRIMARY KEY, role TEXT, removed_at REAL,
                                 kind TEXT, bound_room_id INTEGER);
            CREATE TABLE tasks(id INTEGER PRIMARY KEY, room_id INTEGER, assignee INTEGER,
                               status TEXT, attempts INTEGER DEFAULT 0);
            INSERT INTO members VALUES(1,'member',NULL,'internal',NULL),
                (2,'member',NULL,'internal',NULL),(3,'member',NULL,'external',1),
                (4,'member',NULL,'external',2),(5,'member',99,'internal',NULL);
            """)
        initialize(self.conn)
        self.ids = {1, 2, 3, 4, 5}
        with write_transaction(self.conn):
            self.run = create_run(self.conn, room_id=1, source_message_id=1,
                                  deadline=100, now=1, actor_role="owner",
                                  mode="team", eligible={str(x) for x in self.ids},
                                  leader="1", workers=("2",), reviewer="3")

    def insert(self, task, member, room=1):
        self.conn.execute("INSERT INTO tasks(id,room_id,assignee,status) VALUES(?,?,?,'open')",
                          (task, room, member))

    def bind(self, key, task, member):
        return bind_task(self.conn, self.run, key, task_id=task, member_id=member, now=2)

    def claim(self, task, member):
        fence = reserve_claim(self.conn, task, member_id=member, eligible_ids=self.ids, now=3)
        if fence:
            self.conn.execute("UPDATE tasks SET status='claimed',attempts=attempts+1 WHERE id=?",
                              (task,))
        return fence

    def test_dependencies_and_idempotent_links(self):
        with write_transaction(self.conn):
            self.assertEqual(ready_steps(self.conn, self.run, now=1), [("work-0","2","worker")])
            self.insert(10, 2)
            self.insert(11, 3)
            self.assertTrue(self.bind("work-0", 10, 2))
            self.assertFalse(self.bind("work-0", 10, 2))
            with self.assertRaises(ValueError):
                self.bind("review", 11, 3)
            self.assertEqual(ready_steps(self.conn, self.run, now=2), [])
            self.assertIsNone(self.claim(10, 1))
            self.assertTrue(self.claim(10, 2))
            self.assertIsNone(self.claim(10, 2))
            self.conn.execute("UPDATE tasks SET status='review' WHERE id=10")
            self.assertFalse(reconcile_terminal(self.conn, 10, now=4))
            self.conn.execute("UPDATE tasks SET status='done' WHERE id=10")
            self.assertTrue(reconcile_terminal(self.conn, 10, now=4))
            self.assertTrue(self.bind("review", 11, 3))
            self.assertTrue(self.claim(11, 3))
            self.conn.execute("UPDATE tasks SET status='done' WHERE id=11")
            self.assertTrue(reconcile_terminal(self.conn, 11, now=5))
            self.assertEqual(ready_steps(self.conn, self.run, now=5), [("final","1","synthesis")])
        self.assertEqual(snapshot(self.conn, self.run)["calls_used"], 2)
        self.assertEqual(linked_task(self.conn, 10), (self.run,"work-0",2))

    def test_rollback_task_and_binding_together(self):
        with self.assertRaises(RuntimeError):
            with write_transaction(self.conn):
                self.insert(10, 2)
                self.bind("work-0", 10, 2)
                raise RuntimeError("audit failed")
        self.assertIsNone(linked_task(self.conn, 10))
        self.assertIsNone(self.conn.execute("SELECT id FROM tasks WHERE id=10").fetchone())

    def test_wrong_room_identity_and_reopen(self):
        with write_transaction(self.conn):
            self.insert(10, 2, room=2)
            with self.assertRaises(ValueError):
                self.bind("work-0", 10, 2)
            self.insert(11, 4)
            with self.assertRaises(ValueError):
                self.bind("work-0", 11, 4)
            self.insert(12, 2)
            self.bind("work-0", 12, 2)
            self.assertTrue(self.claim(12, 2))
            self.conn.execute("UPDATE tasks SET status='open' WHERE id=12")
            self.assertTrue(reconcile_terminal(self.conn, 12, now=4))
            self.assertIsNone(self.claim(12, 2))
            self.assertEqual(ready_steps(self.conn, self.run, now=5), [])
        self.assertEqual(snapshot(self.conn, self.run)["status"], "failed")

    def test_cross_room_and_removed_member_rejected_at_binding(self):
        with write_transaction(self.conn):
            for member in (4,5):
                run = create_run(self.conn, room_id=1, source_message_id=member,
                                 deadline=100, now=1, actor_role="owner", mode="focus",
                                 eligible={str(member)}, leader=str(member))
                self.insert(member, member)
                with self.assertRaises(ValueError):
                    bind_task(self.conn, run, "answer", task_id=member, member_id=member, now=2)

    def test_claim_rollback_restores_budget(self):
        with write_transaction(self.conn):
            self.insert(10, 2)
            self.bind("work-0", 10, 2)
        with self.assertRaises(RuntimeError):
            with write_transaction(self.conn):
                self.assertTrue(self.claim(10, 2))
                raise RuntimeError("claim transaction failed")
        self.assertEqual(snapshot(self.conn, self.run)["calls_used"], 0)
        with write_transaction(self.conn):
            self.assertTrue(self.claim(10, 2))


if __name__ == "__main__":
    unittest.main()
