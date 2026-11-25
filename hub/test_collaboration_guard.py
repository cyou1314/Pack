import sqlite3
import unittest

from collaboration_store import initialize, write_transaction, create_run, cancel_run, snapshot
from collaboration_dispatch import bind_task, reserve_claim, reconcile_terminal
from collaboration_guard import install_task_guards, close_linked_tasks


class CollaborationGuardTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.executescript("""
            CREATE TABLE tasks(id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL,
                assignee INTEGER, created_by INTEGER NOT NULL, title TEXT NOT NULL,
                body TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                claim_token TEXT, lease_expires REAL);
            CREATE TABLE members(id INTEGER PRIMARY KEY, role TEXT, removed_at REAL,
                                 kind TEXT, bound_room_id INTEGER);
            INSERT INTO members VALUES(1,'member',NULL,'internal',NULL);
            INSERT INTO tasks(id,room_id,assignee,created_by,title,body,status)
                VALUES(10,1,1,9,'linked','input','open'),
                      (11,1,1,9,'legacy','input','open');
            """)
        initialize(self.conn)
        install_task_guards(self.conn)
        install_task_guards(self.conn)
        with write_transaction(self.conn):
            self.run = create_run(self.conn, room_id=1, source_message_id=1,
                deadline=100, now=1, actor_role="owner", mode="focus",
                eligible={"1"}, leader="1")
            bind_task(self.conn, self.run, "answer", task_id=10, member_id=1, now=2)

    def sql_claim(self, task_id=10):
        self.conn.execute("UPDATE tasks SET status='claimed',attempts=attempts+1,"
                          "claim_token='holder',lease_expires=80 WHERE id=?", (task_id,))

    def reserve(self):
        self.assertTrue(reserve_claim(self.conn, 10, member_id=1, eligible_ids={1}, now=3))
        self.sql_claim()

    def reject(self, sql):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(sql)

    def test_legacy_claim_denied_but_unlinked_task_unaffected(self):
        with write_transaction(self.conn):
            self.reject("UPDATE tasks SET status='claimed',attempts=1,claim_token='x',lease_expires=80 WHERE id=10")
            self.sql_claim(11)
            self.conn.execute("UPDATE tasks SET status='open',claim_token=NULL,lease_expires=NULL WHERE id=11")
        self.assertEqual(snapshot(self.conn,self.run)["calls_used"],0)

    def test_valid_lifecycle_and_no_reopen_or_transfer(self):
        with write_transaction(self.conn):
            self.reserve()
            for sql in (
                "UPDATE tasks SET assignee=2 WHERE id=10",
                "UPDATE tasks SET room_id=2 WHERE id=10",
                "UPDATE tasks SET body='changed' WHERE id=10",
                "UPDATE tasks SET status='open',claim_token=NULL,lease_expires=NULL WHERE id=10",
                "UPDATE tasks SET status='blocked',claim_token=NULL,lease_expires=NULL WHERE id=10",
                "UPDATE tasks SET claim_token=NULL,lease_expires=NULL WHERE id=10",
                "UPDATE tasks SET claim_token='new' WHERE id=10",
                "UPDATE tasks SET attempts=2 WHERE id=10",
                "UPDATE tasks SET status='done' WHERE id=10",
                "DELETE FROM tasks WHERE id=10",
                "INSERT OR REPLACE INTO tasks(id,room_id,assignee,created_by,title,body,status) VALUES(10,1,1,9,'x','x','open')",
            ):
                self.reject(sql)
            self.conn.execute("UPDATE tasks SET status='working',lease_expires=90 WHERE id=10")
            self.conn.execute("UPDATE tasks SET status='review',claim_token=NULL,lease_expires=NULL WHERE id=10")
            self.reject("UPDATE tasks SET status='open' WHERE id=10")
            self.conn.execute("UPDATE tasks SET status='done' WHERE id=10")
            self.assertTrue(reconcile_terminal(self.conn,10,now=4))
        self.assertEqual(snapshot(self.conn,self.run)["status"],"succeeded")

    def test_cancel_cleanup_and_atomic_rollback(self):
        def cancel(conn,task_id):
            conn.execute("UPDATE tasks SET status='cancelled',claim_token=NULL,lease_expires=NULL WHERE id=?",
                         (task_id,))
        with write_transaction(self.conn):
            self.reserve()
        with self.assertRaises(RuntimeError):
            with write_transaction(self.conn):
                cancel_run(self.conn,self.run,now=4)
                close_linked_tasks(self.conn,self.run,cancel_task=lambda conn,task: None)
        self.assertEqual(snapshot(self.conn,self.run)["status"],"active")
        with write_transaction(self.conn):
            self.reject("UPDATE tasks SET status='cancelled',claim_token=NULL,lease_expires=NULL WHERE id=10")
            cancel_run(self.conn,self.run,now=4)
            self.reject("UPDATE tasks SET status='working',lease_expires=90 WHERE id=10")
            self.assertEqual(close_linked_tasks(self.conn,self.run,cancel_task=cancel),(10,))
            self.assertEqual(close_linked_tasks(self.conn,self.run,cancel_task=cancel),())
        self.assertEqual(snapshot(self.conn,self.run)["status"],"cancelled")

    def test_unclaimed_cancel_and_no_dispatch(self):
        def cancel(conn,task_id):
            conn.execute("UPDATE tasks SET status='cancelled' WHERE id=?",(task_id,))
        with write_transaction(self.conn):
            cancel_run(self.conn,self.run,now=4)
            self.assertEqual(close_linked_tasks(self.conn,self.run,cancel_task=cancel),(10,))
            self.assertIsNone(reserve_claim(self.conn,10,member_id=1,eligible_ids={1},now=5))
        self.assertEqual(snapshot(self.conn,self.run)["calls_used"],0)


if __name__ == "__main__":
    unittest.main()
