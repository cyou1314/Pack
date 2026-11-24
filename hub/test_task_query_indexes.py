#!/usr/bin/env python3
"""Task hot-path index migration and SQLite query-plan regression tests."""
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext


EXPECTED_INDEXES = {
    "idx_tasks_urgent_id",
    "idx_tasks_assignee_urgent_id",
    "idx_tasks_room_urgent_id",
    "idx_tasks_active_queue",
    "idx_tasks_lease_expiry",
    "idx_tasks_unclaimed_due",
    "idx_task_transfers_pending_expiry",
}


class TaskQueryIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-task-index-")
        root = Path(self.temp.name)
        self.context = HubContext(
            base_dir=str(root),
            db_path=str(root / "hub.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.RLock(),
            clock=lambda: 10000.0,
        )
        hub.init(self.context)
        self.conn = self.context.connect()
        self.dispatcher = self.conn.execute(
            "SELECT id FROM members WHERE role='dispatcher'"
        ).fetchone()["id"]
        self.member_ids = []
        self.room_ids = []
        for i in range(8):
            cursor = self.conn.execute(
                "INSERT INTO members(name,role,token_hash,created_at) "
                "VALUES(?,?,?,?)",
                (f"index-member-{i}", "member", f"index-token-{i}", 10000.0),
            )
            self.member_ids.append(cursor.lastrowid)
            cursor = self.conn.execute(
                "INSERT INTO rooms(name,created_at) VALUES(?,?)",
                (f"index-room-{i}", 10000.0),
            )
            self.room_ids.append(cursor.lastrowid)

        statuses = ("open", "claimed", "working", "review", "done", "blocked")
        rows = []
        for i in range(2400):
            status = statuses[i % len(statuses)]
            assignee = self.member_ids[i % len(self.member_ids)] if i % 5 else None
            lease_expires = (
                9900.0 + (i % 200)
                if status in ("claimed", "working") else None
            )
            next_claim_at = (
                None if i % 3 == 0
                else 9900.0 if i % 3 == 1
                else 10100.0
            )
            rows.append((
                self.room_ids[i % len(self.room_ids)],
                f"query task {i}", "index fixture", self.dispatcher, assignee,
                status, lease_expires, i % 4, next_claim_at,
                9000.0, 10000.0, i % 2,
            ))
        self.conn.executemany(
            """INSERT INTO tasks(
                   room_id,title,body,created_by,assignee,status,lease_expires,
                   attempts,next_claim_at,created_at,updated_at,urgent
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        self.conn.executemany(
            """INSERT INTO task_transfers(
                   task_id,from_member,to_member,reason,actor,created_at,status,expires_at
               ) VALUES(?,?,?,?,?,?,?,?)""",
            [
                (i + 1, "from", "to", "fixture", "test", 9000.0,
                 "pending" if i % 2 else "accepted",
                 9900.0 if i % 2 else None)
                for i in range(400)
            ],
        )
        self.conn.commit()
        self.conn.execute("ANALYZE")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def plan(self, sql, args=()):
        return " | ".join(
            str(row[3]) for row in self.conn.execute(
                "EXPLAIN QUERY PLAN " + sql, args
            )
        )

    def test_v3_migration_installs_expected_partial_and_ordering_indexes(self):
        versions = {
            row["version"]: row["name"]
            for row in self.conn.execute(
                "SELECT version,name FROM hub_schema_migrations"
            )
        }
        self.assertEqual(
            versions[3], "task_queue_lease_and_transfer_hotpath_indexes"
        )
        task_indexes = {
            row["name"] for row in self.conn.execute("PRAGMA index_list(tasks)")
        }
        transfer_indexes = {
            row["name"]
            for row in self.conn.execute("PRAGMA index_list(task_transfers)")
        }
        self.assertTrue(
            EXPECTED_INDEXES - {"idx_task_transfers_pending_expiry"}
            <= task_indexes
        )
        self.assertIn("idx_task_transfers_pending_expiry", transfer_indexes)

        # Startup is idempotent and migration checksums remain stable.
        hub.init(self.context)
        repeated = {
            row["version"]: row["name"]
            for row in self.conn.execute(
                "SELECT version,name FROM hub_schema_migrations"
            )
        }
        self.assertEqual(repeated, versions)

    def test_task_list_and_sweeper_query_plans_use_hotpath_indexes(self):
        plans = (
            (
                "default task ordering",
                "SELECT id FROM tasks ORDER BY urgent DESC,id ASC LIMIT 100 OFFSET ?",
                (0,),
                "idx_tasks_urgent_id",
            ),
            (
                "assignee task ordering",
                "SELECT id FROM tasks WHERE assignee=? "
                "ORDER BY urgent DESC,id ASC LIMIT 100 OFFSET ?",
                (self.member_ids[0], 0),
                "idx_tasks_assignee_urgent_id",
            ),
            (
                "room task ordering",
                "SELECT id FROM tasks WHERE room_id=? "
                "ORDER BY urgent DESC,id ASC LIMIT 100 OFFSET ?",
                (self.room_ids[0], 0),
                "idx_tasks_room_urgent_id",
            ),
            (
                "active queue rank scan",
                "SELECT id,assignee FROM tasks "
                "WHERE status IN ('open','claimed','working') "
                "ORDER BY urgent DESC,id ASC",
                (),
                "idx_tasks_active_queue",
            ),
            (
                "lease expiry reaper",
                "SELECT * FROM tasks WHERE status IN ('claimed','working') "
                "AND lease_expires IS NOT NULL AND lease_expires<?",
                (10000.0,),
                "idx_tasks_lease_expiry",
            ),
            (
                "unclaimed assigned task sweep",
                "SELECT t.id FROM tasks t JOIN members m ON m.id=t.assignee "
                "WHERE t.status='open' AND t.assignee IS NOT NULL "
                "AND m.removed_at IS NULL "
                "AND (t.next_claim_at IS NULL OR t.next_claim_at<=?)",
                (10000.0,),
                "idx_tasks_unclaimed_due",
            ),
            (
                "pending transfer expiry sweep",
                "SELECT * FROM task_transfers WHERE status='pending' "
                "AND expires_at IS NOT NULL AND expires_at<?",
                (10000.0,),
                "idx_task_transfers_pending_expiry",
            ),
        )
        for name, sql, args, expected in plans:
            with self.subTest(query=name):
                detail = self.plan(sql, args)
                self.assertIn(expected, detail, detail)
                if name == "active queue rank scan":
                    self.assertNotIn("USE TEMP B-TREE FOR ORDER BY", detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
