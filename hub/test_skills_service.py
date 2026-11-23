#!/usr/bin/env python3
"""Isolated skill registry, assignment, rollback and concurrency tests."""
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.skills import (
    list_skills, publish_skill, read_skill, rollback_skill,
    set_skill_assignment,
)


class SkillServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-skill-service-")
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
        self.db.execute(
            "INSERT INTO members(name,role,token_hash,created_at) "
            "VALUES('alice','member','alice-hash',12000)"
        )
        self.db.execute(
            "INSERT INTO members(name,role,token_hash,created_at) "
            "VALUES('bob','member','bob-hash',12000)"
        )
        self.db.execute(
            "INSERT INTO members(name,role,token_hash,created_at,removed_at) "
            "VALUES('removed','member','removed-hash',12000,12400)"
        )
        self.db.commit()
        self.alice = dict(self.db.execute(
            "SELECT * FROM members WHERE name='alice'"
        ).fetchone())

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def publish(self, context=None, actor=None, name="guide", body="v1",
                description="guide desc"):
        return publish_skill(
            context or self.context, actor or self.dispatcher,
            name, body, description,
            body_limit=hub.SKILL_BODY_LIMIT_CHARS,
            description_limit=hub.SKILL_DESC_LIMIT_CHARS,
        )

    def assign(self, context=None, actor=None, name="guide",
               member_name="alice", enabled=True):
        return set_skill_assignment(
            context or self.context, actor or self.dispatcher,
            name, member_name, enabled,
            "skill_assign" if enabled else "skill_unassign",
        )

    def count(self, table, where="", args=()):
        return self.db.execute(
            f"SELECT COUNT(*) FROM {table} {where}", args,
        ).fetchone()[0]

    def test_publish_versions_and_description_retention(self):
        first = self.publish()
        second = self.publish(body="v2", description="")
        third = self.publish(body="v3", description="new description")
        self.assertEqual([first["version"], second["version"], third["version"]], [1, 2, 3])
        skill = self.db.execute(
            "SELECT description,current_version FROM skills WHERE name='guide'"
        ).fetchone()
        self.assertEqual(tuple(skill), ("new description", 3))
        self.assertEqual(self.count("skill_versions"), 3)
        self.assertEqual(read_skill(self.context, self.dispatcher, " guide ")["body"], "v3")

    def test_publish_validates_name_body_and_limits(self):
        for name, body, desc, expected in (
            (" ", "body", "", "skill name"),
            ("guide", "", "", "non-empty"),
            ("guide", None, "", "non-empty"),
        ):
            with self.assertRaisesRegex(ValueError, expected):
                self.publish(name=name, body=body, description=desc)
        with self.assertRaises(ValueError) as error:
            self.publish(body="x" * (hub.SKILL_BODY_LIMIT_CHARS + 1))
        self.assertEqual(json.loads(str(error.exception))["error"], "skill_limit_exceeded")
        with self.assertRaises(ValueError) as error:
            self.publish(body="ok", description="x" * (hub.SKILL_DESC_LIMIT_CHARS + 1))
        self.assertEqual(json.loads(str(error.exception))["error"], "skill_desc_limit_exceeded")
        self.assertEqual(self.count("skills"), 0)
        self.assertEqual(self.count("skill_versions"), 0)

    def test_dispatcher_list_includes_assignees_and_enabled_state(self):
        self.publish()
        self.assign(enabled=True)
        self.db.execute(
            "INSERT INTO member_skills(member_id,skill_id,enabled) "
            "SELECT id,(SELECT id FROM skills WHERE name='guide'),0 "
            "FROM members WHERE name='bob'"
        )
        self.db.commit()
        result = list_skills(self.context, self.dispatcher)
        self.assertEqual(result["skills"][0]["assignees"], [
            {"member": "alice", "enabled": True},
            {"member": "bob", "enabled": False},
        ])

    def test_member_list_only_includes_enabled_assignments(self):
        self.publish()
        self.assign(enabled=True)
        result = list_skills(self.context, self.alice)
        self.assertEqual([x["name"] for x in result["skills"]], ["guide"])
        self.assign(enabled=False)
        self.assertEqual(list_skills(self.context, self.alice), {"skills": []})

    def test_member_read_requires_enabled_assignment(self):
        self.publish()
        with self.assertRaisesRegex(ValueError, "not assigned"):
            read_skill(self.context, self.alice, "guide")
        self.assign(enabled=True)
        self.assertEqual(read_skill(self.context, self.alice, "guide")["body"], "v1")
        self.assign(enabled=False)
        with self.assertRaisesRegex(ValueError, "not assigned"):
            read_skill(self.context, self.alice, "guide")
        with self.assertRaisesRegex(ValueError, "not assigned"):
            read_skill(self.context, self.alice, "missing")

    def test_dispatcher_read_missing_skill_has_precise_error(self):
        with self.assertRaisesRegex(ValueError, "no such skill"):
            read_skill(self.context, self.dispatcher, "missing")

    def test_assignment_validates_skill_member_and_removed_state(self):
        self.publish()
        with self.assertRaisesRegex(ValueError, "no such skill"):
            self.assign(name="missing")
        with self.assertRaisesRegex(ValueError, "no such member"):
            self.assign(member_name="ghost")
        with self.assertRaisesRegex(ValueError, "member is removed"):
            self.assign(member_name="removed")
        self.assertEqual(self.count("member_skills"), 0)

    def test_unassign_absent_is_noop_without_audit(self):
        self.publish()
        result = self.assign(enabled=False)
        self.assertEqual(result, {
            "name": "guide", "member": "alice", "enabled": False,
        })
        self.assertEqual(self.count("member_skills"), 0)
        self.assertEqual(self.count("audit", "WHERE action='skill_unassign'"), 0)

    def test_assign_unassign_writes_metadata_only_audits(self):
        self.publish()
        self.assign(enabled=True)
        self.assign(enabled=False)
        self.assertEqual(self.db.execute(
            "SELECT enabled FROM member_skills"
        ).fetchone()[0], 0)
        rows = self.db.execute(
            "SELECT action,detail FROM audit WHERE action IN "
            "('skill_assign','skill_unassign') ORDER BY id"
        ).fetchall()
        self.assertEqual([row["action"] for row in rows], [
            "skill_assign", "skill_unassign",
        ])
        self.assertTrue(all("guide" in row["detail"] for row in rows))
        self.assertTrue(all("v1" not in row["detail"] for row in rows))

    def test_rollback_appends_new_version_and_preserves_history(self):
        self.publish(body="v1")
        self.publish(body="v2", description="")
        result = rollback_skill(self.context, self.dispatcher, "guide", 1)
        self.assertEqual(result, {
            "name": "guide", "version": 3, "restored_from": 1,
        })
        self.assertEqual(read_skill(self.context, self.dispatcher, "guide")["body"], "v1")
        self.assertEqual(self.count("skill_versions"), 3)

    def test_rollback_missing_version_is_rejected(self):
        self.publish()
        with self.assertRaisesRegex(ValueError, "no such version"):
            rollback_skill(self.context, self.dispatcher, "guide", 99)
        self.assertEqual(self.count("skill_versions"), 1)

    def test_publish_audit_failure_rolls_back_skill_and_version(self):
        self.db.execute(
            "CREATE TRIGGER reject_skill_audit BEFORE INSERT ON audit "
            "WHEN NEW.action='skill_publish' BEGIN "
            "SELECT RAISE(ABORT,'skill publish audit rejected'); END"
        )
        self.db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "skill publish audit rejected"):
            self.publish()
        self.assertEqual(self.count("skills"), 0)
        self.assertEqual(self.count("skill_versions"), 0)

    def test_independent_contexts_serialize_version_allocation(self):
        self.publish()
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

        def submit(context, body):
            try:
                barrier.wait(timeout=10)
                results.append(self.publish(
                    context=context, body=body, description="",
                ))
            except Exception as error:
                errors.append(error)

        threads = [
            threading.Thread(target=submit, args=(self.context, "parallel-a")),
            threading.Thread(target=submit, args=(independent, "parallel-b")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(row["version"] for row in results), [2, 3])
        self.assertEqual(self.count("skill_versions"), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
