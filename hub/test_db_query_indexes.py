#!/usr/bin/env python3
"""Regression tests for structured task audit IDs and Hub query indexes."""
from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from core.context import HubContext
from services.tasks import get_task_audit
import hub


def _seed_pre_index_database(path: Path) -> None:
    """Create the pre-staging57/58 shape with historical rows."""
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE members(
                id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
                role TEXT NOT NULL, token_hash TEXT NOT NULL,
                profile TEXT, last_heartbeat REAL, created_at REAL NOT NULL
            );
            CREATE TABLE rooms(
                id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE messages(
                id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL,
                author_id INTEGER NOT NULL, kind TEXT NOT NULL,
                body TEXT NOT NULL, task_id INTEGER, created_at REAL NOT NULL
            );
            CREATE TABLE tasks(
                id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL,
                title TEXT NOT NULL, body TEXT NOT NULL,
                created_by INTEGER NOT NULL, assignee INTEGER,
                status TEXT NOT NULL, claim_token TEXT, lease_expires REAL,
                attempts INTEGER NOT NULL DEFAULT 0, result TEXT,
                review_note TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE audit(
                id INTEGER PRIMARY KEY, ts REAL NOT NULL, actor TEXT NOT NULL,
                action TEXT NOT NULL, detail TEXT
            );
            CREATE TABLE review_log(
                id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL,
                target_url TEXT NOT NULL, owner_quote TEXT NOT NULL,
                member_desc TEXT NOT NULL, verdict TEXT NOT NULL,
                reviewer TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE task_transfers(
                id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL,
                from_member TEXT NOT NULL, to_member TEXT NOT NULL,
                reason TEXT, actor TEXT NOT NULL, created_at REAL NOT NULL
            );
            INSERT INTO audit VALUES
                (1,1,'legacy','task_update','task 1: first event'),
                (2,2,'legacy','task_update','task 12 second event'),
                (3,3,'legacy','task_update','task 1x must not match'),
                (4,4,'legacy','task_update','task 9223372036854775808 overflow'),
                (5,5,'legacy','task_update','task 0000000000000000000000000000000000001 too long');
            INSERT INTO messages VALUES
                (1,1,1,'approval_request','请批准 任务#1',NULL,1),
                (2,1,1,'approval_request','请批准 任务#12',NULL,2),
                (3,1,1,'approval_request','再次申请 任务#1',NULL,3),
                (4,1,1,'decision','批准 任务#1',NULL,4);
            """
        )
        connection.commit()
    finally:
        connection.close()


def _query_plan_uses(connection, sql: str, args: tuple, index_name: str) -> None:
    plan = " ".join(
        str(row[3])
        for row in connection.execute("EXPLAIN QUERY PLAN " + sql, args).fetchall()
    )
    if index_name not in plan:
        raise AssertionError(f"expected {index_name} in query plan: {plan}")


class HubQueryIndexTests(unittest.TestCase):
    def _context(self, root: Path) -> HubContext:
        return HubContext(
            base_dir=str(root),
            db_path=str(root / "pack.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(),
            clock=lambda: 1000.0,
        )

    def test_init_backfills_structured_ids_and_creates_hot_path_indexes(self):
        with tempfile.TemporaryDirectory(prefix="pack-query-index-") as directory:
            root = Path(directory)
            db_path = root / "pack.db"
            _seed_pre_index_database(db_path)
            context = self._context(root)

            # Existing databases get the additive columns before bootstrap audit
            # writes; historical data is backfilled once and init is repeatable.
            hub.init(context)
            hub.init(context)

            connection = context.connect()
            try:
                self.assertEqual(
                    connection.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                    "wal",
                )
                self.assertEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], 30000)

                audit_rows = connection.execute(
                    "SELECT id,task_id FROM audit WHERE id BETWEEN 1 AND 5 ORDER BY id"
                ).fetchall()
                self.assertEqual(
                    [(row["id"], row["task_id"]) for row in audit_rows],
                    [(1, 1), (2, 12), (3, None), (4, None), (5, None)],
                )

                request_rows = connection.execute(
                    "SELECT id,task_id FROM messages WHERE kind='approval_request' ORDER BY id"
                ).fetchall()
                self.assertEqual(
                    [(row["id"], row["task_id"]) for row in request_rows],
                    [(1, 1), (2, 12), (3, 1)],
                )

                # Indexed rows are preferred; a NULL task_id fallback remains
                # for legacy/direct writers that bypass room_post.
                request, decided_by = hub._approval_state(connection, 1)
                self.assertEqual(request["id"], 3)
                self.assertEqual(decided_by, "message")
                request, decided_by = hub._approval_state(connection, 12)
                self.assertEqual(request["id"], 2)
                self.assertIsNone(decided_by)
                connection.execute(
                    "INSERT INTO messages(room_id,author_id,kind,body,task_id,created_at) "
                    "VALUES(1,2,'approval_request','直接写入 任务#77',77,5)"
                )
                connection.execute(
                    "INSERT INTO messages(room_id,author_id,kind,body,task_id,created_at) "
                    "VALUES(1,2,'approval_request','旧式直写 任务#77',NULL,6)"
                )
                request, _ = hub._approval_state(connection, 77)
                self.assertEqual(request["body"], "旧式直写 任务#77")

                # Seed realistic cardinality before checking planner choices.
                connection.execute(
                    "INSERT INTO members(id,name,role,token_hash,created_at) "
                    "VALUES(2,'worker','member','test-hash',1)"
                )
                connection.executemany(
                    "INSERT INTO tasks(id,room_id,title,body,created_by,assignee,status,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    [
                        (1, 1, "task-one", "body", 1, 2, "open", 1.0, 2.0),
                        (12, 1, "task-twelve", "body", 1, 2, "open", 1.0, 2.0),
                    ],
                )
                task_rows = []
                for number in range(1000, 4000):
                    status = ("open", "claimed", "working", "done")[number % 4]
                    assignee = 2 if number % 3 else None
                    lease = float(number) if status in ("claimed", "working") else None
                    due = float(number) if status == "open" and assignee is not None else None
                    task_rows.append((
                        number, 1, f"task-{number}", "body", 1, assignee, status,
                        None, lease, 0, None, None, None, None, due, 0.0,
                        number % 2, 1.0, 2.0,
                    ))
                connection.executemany(
                    "INSERT INTO tasks("
                    "id,room_id,title,body,created_by,assignee,status,claim_token,"
                    "lease_expires,attempts,result,envelope,review_note,fail_class,"
                    "next_claim_at,sched_at,urgent,created_at,updated_at"
                    ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    task_rows,
                )
                # Historical table's old schema is upgraded with these columns.
                # Populate pending transfers to exercise the expiry partial index.
                connection.executemany(
                    "INSERT INTO task_transfers("
                    "id,task_id,from_member,to_member,reason,actor,created_at,"
                    "status,accepted_at,expires_at"
                    ") VALUES(?,?,?,?,?,?,?,'pending',NULL,?)",
                    [
                        (n, 1000 + n, "a", "b", "reason", "dispatcher", 1.0, float(n))
                        for n in range(1, 501)
                    ],
                )
                connection.executemany(
                    "INSERT INTO review_log("
                    "id,task_id,target_url,owner_quote,member_desc,verdict,reviewer,created_at"
                    ") VALUES(?,?,?,?,?,?,?,?)",
                    [
                        (n, n % 50, "https://example.test", "quote", "desc",
                         "allow", "reviewer", float(n))
                        for n in range(1, 1001)
                    ],
                )
                connection.executemany(
                    "INSERT INTO messages(room_id,author_id,kind,body,task_id,created_at) "
                    "VALUES(1,1,'decision','批准 任务#1',1,?)",
                    [(float(n),) for n in range(10, 1010)],
                )
                connection.execute("ANALYZE")

                plans = (
                    ("SELECT id FROM audit WHERE task_id=? ORDER BY id ASC", (1,), "idx_audit_task"),
                    ("SELECT id FROM messages WHERE kind='approval_request' AND task_id=? "
                     "ORDER BY id DESC LIMIT 1", (1,), "idx_messages_approval_task"),
                    ("SELECT body FROM messages WHERE id>? AND kind IN ('chat','decision') "
                     "AND author_id=? ORDER BY id", (0, 1), "idx_messages_dispatch_decisions"),
                    ("SELECT id FROM review_log WHERE task_id=? AND created_at>=?",
                     (1, 0), "idx_review_log_task_time"),
                    ("SELECT id FROM tasks ORDER BY urgent DESC,id ASC",
                     (), "idx_tasks_urgent_id"),
                    ("SELECT id FROM tasks WHERE assignee=? ORDER BY urgent DESC,id ASC",
                     (2,), "idx_tasks_assignee_urgent_id"),
                    ("SELECT id FROM tasks WHERE room_id=? ORDER BY urgent DESC,id ASC",
                     (1,), "idx_tasks_room_urgent_id"),
                    ("SELECT id FROM tasks WHERE status IN ('open','claimed','working') "
                     "ORDER BY urgent DESC,id ASC", (), "idx_tasks_active_queue"),
                    ("SELECT id FROM tasks WHERE status IN ('claimed','working') "
                     "AND lease_expires IS NOT NULL ORDER BY lease_expires",
                     (), "idx_tasks_lease_expiry"),
                    ("SELECT id FROM tasks WHERE status='open' AND assignee IS NOT NULL "
                     "AND next_claim_at<=?", (2000,), "idx_tasks_unclaimed_due"),
                    ("SELECT id FROM task_transfers WHERE status='pending' "
                     "AND expires_at IS NOT NULL ORDER BY expires_at,id",
                     (), "idx_task_transfers_pending_expiry"),
                )
                for sql, args, index_name in plans:
                    _query_plan_uses(connection, sql, args, index_name)

                context.audit(connection, "worker", "task_update", "task 1: new indexed event")
                connection.commit()
            finally:
                connection.close()

            history = get_task_audit(context, 1)
            event_details = [row["detail"] for row in history["events"]]
            self.assertIn("task 1: first event", event_details)
            self.assertIn("task 1: new indexed event", event_details)
            self.assertNotIn("task 12 second event", event_details)


if __name__ == "__main__":
    unittest.main(verbosity=2)
