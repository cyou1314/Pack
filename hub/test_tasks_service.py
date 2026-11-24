#!/usr/bin/env python3
"""Read-only task history service tests using an isolated SQLite fixture."""
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.tasks import get_task_audit, list_task_transfers, list_tasks


class TaskHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-task-history-")
        root = Path(self.temp.name)
        self.context = HubContext(
            base_dir=str(root), db_path=str(root / "history.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(), clock=lambda: 8000.0,
        )
        hub.init(self.context)
        self.conn = self.context.connect()
        self.dispatcher = self.conn.execute(
            "SELECT id FROM members WHERE role='dispatcher'"
        ).fetchone()[0]
        for task_id in (1, 12):
            self.conn.execute(
                "INSERT INTO tasks(id,room_id,title,body,created_by,assignee,status,"
                "created_at,updated_at) VALUES(?,1,?,'',?,?,'open',8000,8000)",
                (task_id, f"task-{task_id}", self.dispatcher, self.dispatcher),
            )
        self.conn.execute(
            "INSERT INTO runs(task_id,member_id,fence,status,outcome,detail,started_at,ended_at)"
            " VALUES(1,?,1,'closed','done','ok',8001,8002)", (self.dispatcher,),
        )
        self.conn.execute(
            "INSERT INTO task_transfers(task_id,from_member,to_member,reason,"
            "actor,created_at,status,accepted_at) "
            "VALUES(1,'dispatcher','worker','handoff','dispatcher',8003,'accepted',8004)"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_transfer_history_orders_by_id_and_rejects_missing_task(self):
        self.conn.execute(
            "INSERT INTO task_transfers(task_id,from_member,to_member,reason,"
            "actor,created_at,status) "
            "VALUES(1,'worker','dispatcher','return','worker',8005,'expired')"
        )
        self.conn.commit()
        output = list_task_transfers(self.context, 1)
        self.assertEqual(output["task_id"], 1)
        self.assertEqual(
            [t["status"] for t in output["transfers"]], ["accepted", "expired"]
        )
        self.assertEqual(output["transfers"][0]["from_member"], "dispatcher")
        with self.assertRaisesRegex(ValueError, "no such task: 999"):
            list_task_transfers(self.context, 999)

    def test_task_audit_matches_only_exact_task_prefix_and_includes_ledger(self):
        for detail in (
            "task 1", "task 1: assigned", "task 1 completed",
            "task 12 ignored", "task 10 ignored", "task 01 leading zero", "task 1x ignored",
        ):
            self.context.audit(self.conn, "tester", "task_test", detail)
        self.conn.commit()
        output = get_task_audit(self.context, 1)
        self.assertEqual(output["task"]["title"], "task-1")
        self.assertEqual(
            [event["detail"] for event in output["events"]],
            ["task 1", "task 1: assigned", "task 1 completed"],
        )
        self.assertFalse(output["events_truncated"])
        self.assertEqual(len(output["runs"]), 1)
        self.assertEqual(output["runs"][0]["fence"], 1)
        self.assertEqual(len(output["transfers"]), 1)
        self.assertEqual(output["transfers"][0]["status"], "accepted")
        with self.assertRaisesRegex(ValueError, "no such task: 999"):
            get_task_audit(self.context, 999)

    def test_task_audit_caps_at_200_events_and_sets_truncated_flag(self):
        for index in range(205):
            self.context.audit(
                self.conn, "tester", "task_test", f"task 1 event {index:03}"
            )
        self.conn.commit()
        output = get_task_audit(self.context, 1)
        self.assertEqual(len(output["events"]), 200)
        self.assertTrue(output["events_truncated"])
        self.assertEqual(output["events"][0]["detail"], "task 1 event 000")
        self.assertEqual(output["events"][-1]["detail"], "task 1 event 199")


    def test_task_list_priority_status_filter_offset_and_queue_ranks(self):
        self.conn.execute(
            "INSERT INTO tasks(id,room_id,title,body,created_by,assignee,status,"
            "urgent,created_at,updated_at) VALUES(2,1,'priority','',?,?,'claimed',1,8000,8000)",
            (self.dispatcher, self.dispatcher),
        )
        self.conn.execute(
            "INSERT INTO tasks(id,room_id,title,body,created_by,assignee,status,"
            "urgent,created_at,updated_at) VALUES(3,1,'done','',?,?,'done',0,8000,8000)",
            (self.dispatcher, self.dispatcher),
        )
        self.conn.execute(
            "INSERT INTO tasks(id,room_id,title,body,created_by,assignee,status,"
            "urgent,created_at,updated_at) VALUES(4,1,'unassigned','',?,NULL,'open',1,8000,8000)",
            (self.dispatcher,),
        )
        self.conn.commit()
        tasks = list_tasks(self.context, {"kind": "internal"}, "open, claimed, done")["tasks"]
        self.assertEqual([row["id"] for row in tasks], [2, 4, 1, 3, 12])
        ranks = {row["id"]: row["queue_ahead"] for row in tasks}
        self.assertEqual(ranks, {2: 0, 4: 0, 1: 1, 3: None, 12: 2})
        self.assertEqual(
            [r["id"] for r in list_tasks(
                self.context, {"kind": "internal"}, "open,claimed,done", offset=2
            )["tasks"]],
            [1, 3, 12],
        )
        self.assertEqual(
            [r["id"] for r in list_tasks(
                self.context, {"kind": "internal"}, "open", room="nonexistent"
            )["tasks"]],
            [],
        )

    def test_task_list_envelope_backoff_and_negative_offset(self):
        self.conn.execute(
            "UPDATE tasks SET envelope=?,next_claim_at=? WHERE id=1",
            ('{"artifacts": ["one"]}', 8030),
        )
        self.conn.execute("UPDATE tasks SET envelope=? WHERE id=12", ("invalid-json",))
        self.conn.commit()
        result = list_tasks(self.context, {"kind": "internal"}, offset=-200)["tasks"]
        self.assertEqual([row["id"] for row in result], [1, 12])
        self.assertEqual(result[0]["envelope"], {"artifacts": ["one"]})
        self.assertEqual(result[0]["backoff_remaining_sec"], 30)
        self.assertIsNone(result[1]["envelope"])
        self.assertEqual(result[1]["backoff_remaining_sec"], 0)

    def test_external_task_list_ignores_supplied_room_and_assignee_filters(self):
        self.conn.execute(
            "INSERT INTO members(name,role,token_hash,created_at,kind,bound_room_id)"
            " VALUES('external-test','member','test-hash',8000,'external',1)"
        )
        external_id = self.conn.execute(
            "SELECT id FROM members WHERE name='external-test'"
        ).fetchone()[0]
        self.conn.execute(
            "INSERT INTO rooms(name,created_at) VALUES('other-room',8000)"
        )
        other_id = self.conn.execute(
            "SELECT id FROM rooms WHERE name='other-room'"
        ).fetchone()[0]
        for tid, room_id, assignee_id in (
            (15, 1, external_id),
            (16, other_id, external_id),
            (17, 1, None),
        ):
            self.conn.execute(
                "INSERT INTO tasks(id,room_id,title,body,created_by,assignee,status,"
                "created_at,updated_at) VALUES(?,?,?,'',?,?,'open',8000,8000)",
                (tid, room_id, f"task-{tid}", self.dispatcher, assignee_id),
            )
        self.conn.commit()
        me = {"kind": "external", "name": "external-test", "bound_room_id": 1}
        result = list_tasks(
            self.context, me, assignee_name="dispatcher", room="other-room"
        )["tasks"]
        self.assertEqual([row["id"] for row in result], [15])
        self.assertEqual(result[0]["assignee"], "external-test")
        unbound = list_tasks(
            self.context, {"kind": "external", "name": "external-test",
                           "bound_room_id": 999}
        )["tasks"]
        self.assertEqual(unbound, [])

    def test_task_list_caps_page_at_100_with_stable_global_queue_index(self):
        self.conn.executemany(
            "INSERT INTO tasks(id,room_id,title,body,created_by,assignee,status,"
            "created_at,updated_at) VALUES(?,1,?,'',?,?,'open',8000,8000)",
            [(task_id, f"item-{task_id}", self.dispatcher, self.dispatcher)
             for task_id in range(100, 205)],
        )
        self.conn.commit()
        first = list_tasks(self.context, {"kind": "internal"})["tasks"]
        second = list_tasks(self.context, {"kind": "internal"}, offset=100)["tasks"]
        self.assertEqual(len(first), 100)
        self.assertEqual(len(second), 7)
        self.assertEqual([r["id"] for r in first[:3]], [1, 12, 100])
        self.assertEqual([r["id"] for r in second], list(range(198, 205)))
        self.assertEqual([r["queue_ahead"] for r in second], list(range(100, 107)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
