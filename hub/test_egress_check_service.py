#!/usr/bin/env python3
"""Egress decision audit, precedence and cross-context rate-limit regression."""
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.rules import check_egress, upsert_egress_rule


class EgressCheckServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-egress-check-")
        root = Path(self.temp.name)
        self.context = HubContext(
            base_dir=str(root), db_path=str(root / "hub.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(), clock=lambda: 12000.0,
        )
        hub.init(self.context)
        self.db = self.context.connect()
        self.dispatcher = dict(self.db.execute(
            "SELECT * FROM members WHERE role='dispatcher'"
        ).fetchone())
        self.db.execute(
            "INSERT INTO members(name,role,token_hash,created_at,kind,"
            "bound_room_id,joined_msg_id) "
            "VALUES('guest','member','hash',11000,'external',1,0)"
        )
        self.db.execute(
            "INSERT INTO members(name,role,token_hash,created_at) "
            "VALUES('worker','member','hash2',11000)"
        )
        self.db.commit()
        self.guest = dict(self.db.execute(
            "SELECT * FROM members WHERE name='guest'"
        ).fetchone())
        self.worker = dict(self.db.execute(
            "SELECT * FROM members WHERE name='worker'"
        ).fetchone())

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def check(self, *, member=None, url="https://example.com/doc",
              task_id=0, rate_limit=60, context=None):
        return check_egress(
            context or self.context, member or self.dispatcher,
            url, task_id, decide=hub._egress_decide,
            rate_limit=rate_limit, rate_error=hub._ext_rate_error,
        )

    def audits(self, action):
        return self.db.execute(
            "SELECT actor,detail FROM audit WHERE action=? ORDER BY id",
            (action,),
        ).fetchall()

    def make_task(self, creator=None, title="Read example.com", body="details"):
        return self.db.execute(
            "INSERT INTO tasks(room_id,title,body,created_by,status,"
            "attempts,created_at,updated_at) "
            "VALUES(1,?,?,?,'open',0,12000,12000)",
            (title, body, (creator or self.dispatcher)["id"]),
        ).lastrowid

    def test_no_rule_defaults_ask_and_writes_audit(self):
        result = self.check()
        self.assertEqual(
            result, {"decision": "ask", "host": "example.com",
                     "source": "none", "task_id": 0},
        )
        rows = self.audits("egress_check")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["actor"], "egress")
        self.assertIn("decision=ask", rows[0]["detail"])

    def test_explicit_rule_overrides_default(self):
        rule = upsert_egress_rule(
            self.context, self.dispatcher, "example.com", "allow",
        )
        result = self.check(url="https://a.example.com/path")
        self.assertEqual(result["decision"], "allow")
        self.assertEqual(result["source"], f"rule#{rule['rule_id']}")

    def test_dispatcher_task_mention_allows_without_rule(self):
        task_id = self.make_task()
        self.db.commit()
        result = self.check(task_id=task_id)
        self.assertEqual(result["decision"], "allow")
        self.assertEqual(result["source"], f"task#{task_id}-mention")

    def test_member_task_mention_cannot_self_authorize(self):
        task_id = self.make_task(creator=self.worker)
        self.db.commit()
        result = self.check(member=self.worker, task_id=task_id)
        self.assertEqual(result["decision"], "ask")

    def test_ask_rule_overrides_dispatcher_task_mention(self):
        task_id = self.make_task()
        self.db.commit()
        upsert_egress_rule(
            self.context, self.dispatcher, "example.com", "ask",
        )
        self.assertEqual(self.check(task_id=task_id)["decision"], "ask")

    def test_invalid_url_audit_does_not_leak_query_secret(self):
        with self.assertRaisesRegex(ValueError, "malformed url"):
            self.check(url="ftp://example.com/private?token=SECRET#fragment")
        rows = self.audits("egress_check")
        self.assertEqual(len(rows), 1)
        detail = rows[0]["detail"]
        self.assertIn("decision=reject", detail)
        self.assertNotIn("SECRET", detail)
        self.assertNotIn("token=", detail)

    def test_unknown_task_id_is_rejected_and_audited(self):
        with self.assertRaisesRegex(ValueError, "no such task"):
            self.check(task_id=999999)
        self.assertEqual(len(self.audits("egress_check")), 1)
        self.assertIn("decision=reject", self.audits("egress_check")[0]["detail"])

    def test_external_throttle_is_persistent_and_audited(self):
        self.check(member=self.guest, rate_limit=2)
        self.check(member=self.guest, rate_limit=2)
        with self.assertRaises(ValueError) as error:
            self.check(member=self.guest, rate_limit=2)
        self.assertEqual(json.loads(str(error.exception))["retry_after"], 60)
        self.assertEqual(len(self.audits("egress_check")), 2)
        self.assertEqual(len(self.audits("external_egress_throttled")), 1)

    def test_regular_member_has_no_external_rate_cap(self):
        for _ in range(3):
            self.assertEqual(
                self.check(member=self.worker, rate_limit=1)["decision"], "ask",
            )
        self.assertEqual(len(self.audits("egress_check")), 3)
        self.assertEqual(len(self.audits("external_egress_throttled")), 0)

    def test_failed_decision_audit_does_not_commit(self):
        self.db.execute(
            "CREATE TRIGGER reject_decision BEFORE INSERT ON audit "
            "WHEN NEW.action='egress_check' BEGIN "
            "SELECT RAISE(ABORT,'egress audit rejected'); END"
        )
        self.db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "egress audit rejected"):
            self.check()
        self.assertEqual(len(self.audits("egress_check")), 0)

    def test_failed_throttle_audit_does_not_write_denial(self):
        self.check(member=self.guest, rate_limit=1)
        self.db.execute(
            "CREATE TRIGGER reject_throttle BEFORE INSERT ON audit "
            "WHEN NEW.action='external_egress_throttled' BEGIN "
            "SELECT RAISE(ABORT,'throttle audit rejected'); END"
        )
        self.db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "throttle audit rejected"):
            self.check(member=self.guest, rate_limit=1)
        self.assertEqual(len(self.audits("external_egress_throttled")), 0)
        self.assertEqual(len(self.audits("egress_check")), 1)

    def test_two_independent_hub_contexts_share_external_limit(self):
        root = Path(self.temp.name)
        independent = HubContext(
            base_dir=str(root), db_path=str(root / "hub.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(), clock=lambda: 12000.0,
        )
        barrier = threading.Barrier(2)
        results = []
        def submit(context):
            try:
                barrier.wait(timeout=10)
                response = self.check(
                    member=self.guest, rate_limit=1, context=context,
                )
                results.append(response["decision"])
            except ValueError as error:
                results.append(json.loads(str(error))["error"])

        threads = [
            threading.Thread(target=submit, args=(self.context,)),
            threading.Thread(target=submit, args=(independent,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertCountEqual(results, ["ask", "rate_limited"])
        self.assertEqual(len(self.audits("egress_check")), 1)
        self.assertEqual(len(self.audits("external_egress_throttled")), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
