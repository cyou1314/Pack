#!/usr/bin/env python3
"""Focused unit tests for production Hub policy helpers (not staging snapshots)."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import hub


class HubPolicyHelperTests(unittest.TestCase):
    def test_action_tag_uses_first_supported_marker(self):
        self.assertEqual(hub.action_tag("[ENG] [M4A-image] review"), "[ENG]")
        self.assertEqual(hub.action_tag("  no action marker"), "")
        self.assertEqual(hub.action_tag(None), "")

    def test_approval_rule_specificity_and_disabled_rows(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE approval_rules(
                id INTEGER PRIMARY KEY, action TEXT NOT NULL, target TEXT NOT NULL,
                effect TEXT NOT NULL, enabled INTEGER NOT NULL
            )
        """)
        conn.executemany(
            "INSERT INTO approval_rules(action,target,effect,enabled) VALUES(?,?,?,?)",
            [
                ("", "", "allow", 1),
                ("[ENG]", "", "block", 1),
                ("", "alice", "allow", 1),
                ("[ENG]", "alice", "allow", 1),
                ("[M4A]", "alice", "block", 0),
            ],
        )
        self.assertEqual(hub.rule_eval(conn, "[ENG]", "alice")["effect"], "allow")
        conn.execute("DELETE FROM approval_rules WHERE action='[ENG]' AND target='alice'")
        self.assertEqual(hub.rule_eval(conn, "[ENG]", "alice")["effect"], "block")
        conn.execute("DELETE FROM approval_rules WHERE action='[ENG]' AND target=''")
        self.assertEqual(hub.rule_eval(conn, "[ENG]", "alice")["effect"], "allow")
        conn.execute("DELETE FROM approval_rules WHERE action='' AND target='alice'")
        self.assertEqual(hub.rule_eval(conn, "[ENG]", "alice")["effect"], "allow")
        conn.close()

    def test_access_mode_defaults_and_executor_pin_policy(self):
        self.assertEqual(hub._member_access_mode({"name": "legacy"}), "full")
        self.assertEqual(hub._member_access_mode({"access_mode": "bad-value"}), "full")
        self.assertEqual(hub._member_access_mode({"access_mode": "readonly"}), "readonly")
        self.assertTrue(hub._is_executor_pinned("  [ENG] inspect", "ordinary body"))
        self.assertTrue(hub._is_executor_pinned("ordinary title", "contains [ENG-image] marker"))
        self.assertFalse(hub._is_executor_pinned("ordinary title", "ordinary body"))

    def test_access_mode_gate_allows_and_denies_with_audit(self):
        with tempfile.TemporaryDirectory(prefix="pack-hub-policy-") as directory:
            database = str(Path(directory) / "hub.db")
            with patch.object(hub, "DB_PATH", database):
                conn = hub.db()
                conn.executescript(hub.SCHEMA)
                conn.close()

                # Full mode is backwards compatible; readonly and workspace
                # modes apply explicit allow/deny policy.
                hub._mode_check({"name": "legacy"}, "task_create")
                hub._mode_check({"name": "reader", "access_mode": "readonly"}, "task_list")
                hub._mode_check({"name": "worker", "access_mode": "workspace"}, "task_claim")

                with self.assertRaisesRegex(ValueError, "readonly access mode"):
                    hub._mode_check({"name": "reader", "access_mode": "readonly"}, "task_create")
                with self.assertRaisesRegex(ValueError, "workspace access mode"):
                    hub._mode_check({"name": "worker", "access_mode": "workspace"}, "egress_check")

                conn = hub.db()
                rows = conn.execute(
                    "SELECT actor, action, detail FROM audit ORDER BY id"
                ).fetchall()
                conn.close()
                self.assertEqual(
                    [(row["actor"], row["action"]) for row in rows],
                    [("reader", "access_mode_denied"), ("worker", "access_mode_denied")],
                )
                self.assertIn("tool=task_create mode=readonly", rows[0]["detail"])
                self.assertIn("tool=egress_check mode=workspace", rows[1]["detail"])

    def test_approval_gate_only_accepts_holder_or_dispatcher(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE tasks(id INTEGER PRIMARY KEY, title TEXT, assignee INTEGER);
            CREATE TABLE members(id INTEGER PRIMARY KEY, name TEXT, role TEXT);
            CREATE TABLE messages(
                id INTEGER PRIMARY KEY, room_id INTEGER, author_id INTEGER,
                kind TEXT, body TEXT, task_id INTEGER, created_at REAL
            );
            CREATE TABLE approval_rules(
                id INTEGER PRIMARY KEY, action TEXT, target TEXT,
                effect TEXT, enabled INTEGER
            );
            CREATE TABLE audit(
                id INTEGER PRIMARY KEY, ts REAL, actor TEXT, action TEXT, detail TEXT
            );
            INSERT INTO members(id,name,role) VALUES
                (1,'dispatcher','dispatcher'),
                (2,'alice','member'),
                (3,'bob','member');
            INSERT INTO tasks(id,title,assignee) VALUES (9,'[ENG] inspect',2);
            INSERT INTO approval_rules(id,action,target,effect,enabled)
                VALUES (1,'[ENG]','alice','allow',1);
            INSERT INTO messages(id,room_id,author_id,kind,body,task_id,created_at)
                VALUES
                (10,1,2,'approval_request','任务#9 需要批准',NULL,1),
                (11,1,3,'approval_request','任务#9 需要批准',NULL,2),
                (12,1,1,'approval_request','任务#9 需要批准',NULL,3);
        """)

        alice = conn.execute("SELECT * FROM members WHERE id=2").fetchone()
        bob = conn.execute("SELECT * FROM members WHERE id=3").fetchone()
        dispatcher = conn.execute("SELECT * FROM members WHERE id=1").fetchone()

        allowed = hub._approval_gate(conn, 1, 10, "任务#9 需要批准", poster=alice)
        self.assertEqual(allowed["auto_decision"], "allow")
        self.assertEqual(allowed["task_id"], 9)

        denied = hub._approval_gate(conn, 1, 11, "任务#9 需要批准", poster=bob)
        self.assertEqual(denied, {})
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM messages WHERE kind='decision'").fetchone()[0],
            1,
        )

        dispatcher_allowed = hub._approval_gate(
            conn, 1, 12, "任务#9 需要批准", poster=dispatcher
        )
        self.assertEqual(dispatcher_allowed["auto_decision"], "allow")
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM messages WHERE kind='decision'").fetchone()[0],
            2,
        )
        audit_rows = conn.execute(
            "SELECT actor, action FROM audit ORDER BY id"
        ).fetchall()
        self.assertEqual(
            [(row["actor"], row["action"]) for row in audit_rows],
            [("rule-engine", "approval_auto_allow"), ("rule-engine", "approval_auto_allow")],
        )
        conn.close()

    def test_backoff_policy_is_exponential_and_bounded_at_first_attempt(self):
        self.assertEqual(hub._backoff_seconds("quota", 1), 1800)
        self.assertEqual(hub._backoff_seconds("quota", 2), 3600)
        self.assertEqual(hub._backoff_seconds("transient", 3), 240)
        self.assertEqual(hub._backoff_seconds("unknown", 0), 300)


if __name__ == "__main__":
    unittest.main(verbosity=2)
