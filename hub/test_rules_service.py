#!/usr/bin/env python3
"""Isolated rule CRUD, audit atomicity, and cross-context write tests."""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.rules import (
    delete_approval_rule,
    delete_egress_rule,
    list_approval_rules,
    list_egress_rules,
    upsert_approval_rule,
    upsert_egress_rule,
)


class RuleServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-rule-service-")
        root = Path(self.temp.name)
        self.context = HubContext(
            base_dir=str(root), db_path=str(root / "hub.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(), clock=lambda: 12500.0,
        )
        hub.init(self.context)
        self.db = self.context.connect()
        self.actor = dict(self.db.execute(
            "SELECT * FROM members WHERE role='dispatcher'"
        ).fetchone())

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def count(self, table):
        return self.db.execute(
            "SELECT COUNT(*) FROM " + table
        ).fetchone()[0]

    def audit_count(self, action):
        return self.db.execute(
            "SELECT COUNT(*) FROM audit WHERE action=?", (action,)
        ).fetchone()[0]

    def test_empty_lists_and_saved_metadata(self):
        self.assertEqual(list_approval_rules(self.context), {"rules": []})
        self.assertEqual(list_egress_rules(self.context), {"rules": []})
        approval = upsert_approval_rule(
            self.context, self.actor, "[ENG]", "alice", "block", "policy",
        )
        egress = upsert_egress_rule(
            self.context, self.actor, "example.com", "ask", "review",
        )
        self.assertEqual(approval["action"], "[ENG]")
        self.assertEqual(egress["effect"], "ask")
        approval_row = list_approval_rules(self.context)["rules"][0]
        egress_row = list_egress_rules(self.context)["rules"][0]
        self.assertEqual(approval_row["source"], "policy")
        self.assertEqual(approval_row["created_at"], 12500.0)
        self.assertEqual(egress_row["created_by"], self.actor["name"])
        self.assertEqual(egress_row["enabled"], 1)

    def test_upsert_approval_is_idempotent_and_trims_key(self):
        first = upsert_approval_rule(
            self.context, self.actor, " [ENG] ", " alice ", "allow",
        )
        second = upsert_approval_rule(
            self.context, self.actor, "[ENG]", "alice", "block", "changed",
        )
        self.assertEqual(first["rule_id"], second["rule_id"])
        self.assertEqual(self.count("approval_rules"), 1)
        row = list_approval_rules(self.context)["rules"][0]
        self.assertEqual((row["action"], row["target"]), ("[ENG]", "alice"))
        self.assertEqual((row["effect"], row["source"]), ("block", "changed"))
        self.assertEqual(self.audit_count("rule_upsert"), 2)

    def test_disabled_approval_rule_is_reenabled(self):
        original = upsert_approval_rule(
            self.context, self.actor, "", "", "allow",
        )
        self.db.execute(
            "UPDATE approval_rules SET enabled=0 WHERE id=?",
            (original["rule_id"],),
        )
        self.db.commit()
        again = upsert_approval_rule(
            self.context, self.actor, "", "", "block",
        )
        self.assertEqual(again["rule_id"], original["rule_id"])
        self.assertEqual(list_approval_rules(self.context)["rules"][0]["enabled"], 1)

    def test_bad_approval_effect_does_not_write(self):
        with self.assertRaisesRegex(ValueError, "effect must"):
            upsert_approval_rule(
                self.context, self.actor, "[ENG]", "alice", "ask",
            )
        self.assertEqual(self.count("approval_rules"), 0)
        self.assertEqual(self.audit_count("rule_upsert"), 0)

    def test_delete_approval_and_missing_id_are_distinct(self):
        created = upsert_approval_rule(
            self.context, self.actor, "task", "alice", "allow",
        )
        self.assertEqual(
            delete_approval_rule(self.context, self.actor, created["rule_id"]),
            {"deleted": created["rule_id"]},
        )
        with self.assertRaisesRegex(ValueError, "no such rule"):
            delete_approval_rule(self.context, self.actor, created["rule_id"])
        self.assertEqual(self.count("approval_rules"), 0)
        self.assertEqual(self.audit_count("rule_delete"), 1)

    def test_egress_pattern_normalization_and_toggle(self):
        first = upsert_egress_rule(
            self.context, self.actor, "*.Example.COM.", "allow",
        )
        second = upsert_egress_rule(
            self.context, self.actor, "example.com", "ask", "source2",
        )
        self.assertEqual(first["pattern"], "example.com")
        self.assertEqual(first["rule_id"], second["rule_id"])
        self.assertEqual(self.count("egress_rules"), 1)
        row = list_egress_rules(self.context)["rules"][0]
        self.assertEqual((row["effect"], row["source"]), ("ask", "source2"))
        self.assertEqual(self.audit_count("egress_rule_upsert"), 2)

    def test_bad_egress_effect_and_pattern_do_not_write(self):
        with self.assertRaisesRegex(ValueError, "effect must"):
            upsert_egress_rule(
                self.context, self.actor, "example.com", "block",
            )
        with self.assertRaisesRegex(ValueError, "malformed egress pattern"):
            upsert_egress_rule(
                self.context, self.actor, "../secret", "allow",
            )
        self.assertEqual(self.count("egress_rules"), 0)

    def test_delete_egress_and_missing_id(self):
        created = upsert_egress_rule(
            self.context, self.actor, "example.com", "allow",
        )
        self.assertEqual(
            delete_egress_rule(self.context, self.actor, created["rule_id"]),
            {"deleted": created["rule_id"]},
        )
        with self.assertRaisesRegex(ValueError, "no such egress rule"):
            delete_egress_rule(self.context, self.actor, created["rule_id"])
        self.assertEqual(self.audit_count("egress_rule_delete"), 1)

    def test_failed_approval_upsert_audit_rolls_back(self):
        self.db.execute(
            "CREATE TRIGGER deny_approval_audit BEFORE INSERT ON audit "
            "WHEN NEW.action='rule_upsert' BEGIN "
            "SELECT RAISE(ABORT,'approval audit rejected'); END"
        )
        self.db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "approval audit rejected"):
            upsert_approval_rule(
                self.context, self.actor, "[ENG]", "alice", "allow",
            )
        self.assertEqual(self.count("approval_rules"), 0)

    def test_failed_egress_upsert_audit_rolls_back(self):
        self.db.execute(
            "CREATE TRIGGER deny_egress_audit BEFORE INSERT ON audit "
            "WHEN NEW.action='egress_rule_upsert' BEGIN "
            "SELECT RAISE(ABORT,'egress audit rejected'); END"
        )
        self.db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "egress audit rejected"):
            upsert_egress_rule(
                self.context, self.actor, "example.com", "allow",
            )
        self.assertEqual(self.count("egress_rules"), 0)

    def test_failed_approval_delete_audit_restores_row(self):
        created = upsert_approval_rule(
            self.context, self.actor, "[ENG]", "alice", "allow",
        )
        self.db.execute(
            "CREATE TRIGGER deny_delete_audit BEFORE INSERT ON audit "
            "WHEN NEW.action='rule_delete' BEGIN "
            "SELECT RAISE(ABORT,'rule delete audit rejected'); END"
        )
        self.db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "rule delete audit rejected"):
            delete_approval_rule(self.context, self.actor, created["rule_id"])
        self.assertEqual(self.count("approval_rules"), 1)

    def test_failed_egress_delete_audit_restores_row(self):
        created = upsert_egress_rule(
            self.context, self.actor, "example.com", "allow",
        )
        self.db.execute(
            "CREATE TRIGGER deny_delete_audit BEFORE INSERT ON audit "
            "WHEN NEW.action='egress_rule_delete' BEGIN "
            "SELECT RAISE(ABORT,'egress delete audit rejected'); END"
        )
        self.db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "egress delete audit rejected"):
            delete_egress_rule(self.context, self.actor, created["rule_id"])
        self.assertEqual(self.count("egress_rules"), 1)

    def test_independent_hub_contexts_serialize_conflicting_upserts(self):
        root = Path(self.temp.name)
        independent = HubContext(
            base_dir=str(root), db_path=str(root / "hub.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(), clock=lambda: 12500.0,
        )
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def submit(context, effect):
            try:
                barrier.wait(timeout=10)
                results.append(upsert_egress_rule(
                    context, self.actor, "example.com", effect,
                ))
            except Exception as error:
                errors.append(error)

        threads = [
            threading.Thread(target=submit, args=(self.context, "allow")),
            threading.Thread(target=submit, args=(independent, "ask")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["rule_id"], results[1]["rule_id"])
        self.assertEqual(self.count("egress_rules"), 1)
        self.assertEqual(self.audit_count("egress_rule_upsert"), 2)
        self.assertIn(
            list_egress_rules(self.context)["rules"][0]["effect"],
            ("allow", "ask"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
