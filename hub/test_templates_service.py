#!/usr/bin/env python3
"""Isolated task-template validation, CRUD and firing tests."""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.templates import (
    delete_template, fire_template, list_templates, upsert_template,
)


class TemplateServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-template-service-")
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
            "INSERT INTO members(name,role,token_hash,created_at,removed_at) "
            "VALUES('removed','member','removed-hash',12000,12400)"
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def upsert(self, name="web", title="Review {arg}", body="Read {arg}",
               assignee="alice"):
        return upsert_template(
            self.context, self.dispatcher, name, title, body, assignee,
        )

    def count(self, table, where="", args=()):
        return self.db.execute(
            f"SELECT COUNT(*) FROM {table} {where}", args,
        ).fetchone()[0]

    def test_upsert_creates_and_updates_template(self):
        first = self.upsert()
        second = self.upsert(title="Check {arg}", body="New body", assignee="")
        self.assertEqual(first["template_id"], second["template_id"])
        row = list_templates(self.context)["templates"][0]
        self.assertEqual(row["title_tpl"], "Check {arg}")
        self.assertEqual(row["body_tpl"], "New body")
        self.assertEqual(row["assignee"], "")
        self.assertEqual(row["updated_by"], self.dispatcher["name"])
        self.assertEqual(self.count("audit", "WHERE action='template_upsert'"), 2)

    def test_upsert_validates_lengths(self):
        for args, expected in (
            (("", "title", "body", ""), "template name"),
            (("name", "", "body", ""), "title_tpl"),
            (("name", "x" * 201, "body", ""), "title_tpl"),
            (("name", "title", "x" * 4001, ""), "body_tpl too long"),
        ):
            with self.assertRaisesRegex(ValueError, expected):
                upsert_template(self.context, self.dispatcher, *args)
        self.assertEqual(self.count("task_templates"), 0)

    def test_upsert_checks_assignee_and_removed_member(self):
        with self.assertRaisesRegex(ValueError, "no such member"):
            self.upsert(assignee="ghost")
        with self.assertRaisesRegex(ValueError, "member is removed"):
            self.upsert(assignee="removed")
        self.assertEqual(self.count("task_templates"), 0)

    def test_list_returns_all_template_fields_in_id_order(self):
        self.upsert(name="one")
        self.upsert(name="two")
        rows = list_templates(self.context)["templates"]
        self.assertEqual([row["name"] for row in rows], ["one", "two"])
        self.assertEqual(
            set(rows[0]),
            {"id", "name", "title_tpl", "body_tpl", "assignee",
             "updated_at", "updated_by"},
        )

    def test_delete_and_missing_template(self):
        self.upsert()
        self.assertEqual(delete_template(self.context, self.dispatcher, "web"),
                         {"deleted": "web"})
        with self.assertRaisesRegex(ValueError, "no such template"):
            delete_template(self.context, self.dispatcher, "web")
        self.assertEqual(self.count("task_templates"), 0)
        self.assertEqual(self.count("audit", "WHERE action='template_delete'"), 1)

    def test_fire_expands_arg_and_uses_template_assignee(self):
        self.upsert()
        calls = []
        def create_task(**kwargs):
            calls.append(kwargs)
            return {"task_id": 41, "status": "open"}
        result = fire_template(
            self.context, self.dispatcher, "web", "API",
            create_task=create_task,
        )
        self.assertEqual(calls, [{
            "room": "pack-main", "title": "Review API",
            "body": "Read API", "assignee_name": "alice",
        }])
        self.assertEqual(result, {
            "task_id": 41, "status": "open", "template": "web",
        })
        self.assertEqual(self.count("audit", "WHERE action='template_fire'"), 1)

    def test_fire_override_and_custom_room(self):
        self.upsert()
        calls = []
        result = fire_template(
            self.context, self.dispatcher, "web", "B",
            room="other-room", assignee_override="override",
            create_task=lambda **kwargs: calls.append(kwargs) or {"task_id": 42},
        )
        self.assertEqual(calls[0]["room"], "other-room")
        self.assertEqual(calls[0]["assignee_name"], "override")
        self.assertEqual(result["template"], "web")

    def test_fire_requires_valid_placeholder_and_argument(self):
        self.upsert(title="Static title", body="Static body")
        with self.assertRaisesRegex(ValueError, "no .*placeholder"):
            fire_template(
                self.context, self.dispatcher, "web", "unexpected",
                create_task=lambda **kwargs: {"task_id": 1},
            )
        self.upsert(title="Need {arg}", body="Static body")
        with self.assertRaisesRegex(ValueError, "requires an arg"):
            fire_template(
                self.context, self.dispatcher, "web", "  ",
                create_task=lambda **kwargs: {"task_id": 1},
            )
        with self.assertRaisesRegex(ValueError, "arg too long"):
            fire_template(
                self.context, self.dispatcher, "web", "x" * 1001,
                create_task=lambda **kwargs: {"task_id": 1},
            )

    def test_fire_rejects_removed_assignee_and_missing_template(self):
        upsert_template(
            self.context, self.dispatcher, "removed-default",
            "Title", "Body", "removed",
        )
        with self.assertRaisesRegex(ValueError, "member is removed"):
            fire_template(
                self.context, self.dispatcher, "removed-default",
                create_task=lambda **kwargs: {"task_id": 1},
            )
        with self.assertRaisesRegex(ValueError, "no such template"):
            fire_template(
                self.context, self.dispatcher, "missing",
                create_task=lambda **kwargs: {"task_id": 1},
            )

    def test_missing_assignee_is_delegated_to_task_create(self):
        self.upsert(assignee="ghost")
        calls = []
        def create_task(**kwargs):
            calls.append(kwargs)
            raise ValueError("no such member: ghost")
        with self.assertRaisesRegex(ValueError, "no such member: ghost"):
            fire_template(
                self.context, self.dispatcher, "web",
                create_task=create_task,
            )
        self.assertEqual(len(calls), 1)

    def test_upsert_audit_failure_rolls_back_template(self):
        self.db.execute(
            "CREATE TRIGGER reject_template_audit BEFORE INSERT ON audit "
            "WHEN NEW.action='template_upsert' BEGIN "
            "SELECT RAISE(ABORT,'template audit rejected'); END"
        )
        self.db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "template audit rejected"):
            self.upsert()
        self.assertEqual(self.count("task_templates"), 0)

    def test_fire_audit_failure_does_not_retroactively_undo_task_create(self):
        self.upsert()
        calls = []
        self.db.execute(
            "CREATE TRIGGER reject_template_fire_audit BEFORE INSERT ON audit "
            "WHEN NEW.action='template_fire' BEGIN "
            "SELECT RAISE(ABORT,'template fire audit rejected'); END"
        )
        self.db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "template fire audit rejected"):
            fire_template(
                self.context, self.dispatcher, "web", "API",
                create_task=lambda **kwargs: calls.append(kwargs) or {"task_id": 77},
            )
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
