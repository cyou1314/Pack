#!/usr/bin/env python3
"""Isolated memory read/version services and authorization regression."""
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.memory import read_memory, read_memory_versions


class MemoryReadServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-memory-read-")
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
        self.db.commit()
        self.alice = dict(self.db.execute(
            "SELECT * FROM members WHERE name='alice'"
        ).fetchone())
        self.bob = dict(self.db.execute(
            "SELECT * FROM members WHERE name='bob'"
        ).fetchone())
        self.v1, _, _ = hub._mem_append_version(
            self.db, "alice", "private", "notes", "first version",
            "alice", "self", [{"type": "task", "id": 7}], self.context,
        )
        self.v2, _, _ = hub._mem_append_version(
            self.db, "alice", "private", "notes", "second version",
            "alice", "external", [{"type": "message", "id": 12}], self.context,
        )
        self.shared_version, _, _ = hub._mem_append_version(
            self.db, hub.MEM_ROOM_MEMBER, "room-shared", "",
            "shared note", "dispatcher", "dispatcher", [], self.context,
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def resolve(self, conn, actor, scope, member):
        return hub._mem_target(conn, actor, scope, member, self.context)

    def denied(self, conn, actor, target, scope, what):
        return hub._mem_denied(
            conn, actor, target, scope, what, context=self.context,
        )

    def read(self, actor=None, scope="private", key="notes", member=""):
        return read_memory(
            self.context, actor or self.alice, scope, key, member,
            validate_scope_key=hub._mem_validate_scope_key,
            resolve_target=self.resolve,
            current_value=hub._mem_current,
        )

    def versions(self, actor=None, scope="private", key="notes", limit=20,
                 before_id=0, include_text=False, member=""):
        return read_memory_versions(
            self.context, actor or self.alice, scope, key, limit,
            before_id, include_text, member,
            validate_scope_key=hub._mem_validate_scope_key,
            resolve_target=self.resolve, denied=self.denied,
        )

    def audit_details(self):
        return self.db.execute(
            "SELECT detail FROM audit WHERE action='memory_access_denied' ORDER BY id"
        ).fetchall()

    def test_read_current_value_and_origin(self):
        result = self.read()
        self.assertEqual(result, {
            "member": "alice", "scope": "private", "key": "notes",
            "text": "second version", "version_id": self.v2,
            "origin": "external", "updated_at": 12500.0,
        })

    def test_missing_value_returns_stable_empty_shape(self):
        self.assertEqual(self.read(scope="private-core", key=""), {
            "member": "alice", "scope": "private-core", "key": "",
            "text": "", "version_id": None, "origin": None, "updated_at": None,
        })

    def test_room_shared_ignores_member_override(self):
        result = self.read(scope="room-shared", key="", member="bob")
        self.assertEqual(result["member"], hub.MEM_ROOM_MEMBER)
        self.assertEqual(result["text"], "shared note")
        self.assertEqual(result["version_id"], self.shared_version)

    def test_dispatcher_can_read_another_members_memory(self):
        result = self.read(actor=self.dispatcher, member="alice")
        self.assertEqual(result["member"], "alice")
        self.assertEqual(result["text"], "second version")

    def test_member_cross_member_read_is_denied_and_audited(self):
        with self.assertRaisesRegex(ValueError, "forbidden"):
            self.read(actor=self.alice, member="bob")
        self.assertEqual(len(self.audit_details()), 1)
        self.assertIn('"what": "cross-member"', self.audit_details()[0]["detail"])

    def test_versions_are_newest_first_with_source_refs(self):
        result = self.versions()
        self.assertEqual(
            [item["version_id"] for item in result["versions"]],
            [self.v2, self.v1],
        )
        self.assertEqual(
            result["versions"][0]["source_refs"],
            [{"type": "message", "id": 12}],
        )
        self.assertNotIn("text", result["versions"][0])

    def test_versions_support_cursor_and_clamped_limit(self):
        result = self.versions(limit=0)
        self.assertEqual(
            [item["version_id"] for item in result["versions"]], [self.v2],
        )
        older = self.versions(before_id=self.v2)
        self.assertEqual(
            [item["version_id"] for item in older["versions"]], [self.v1],
        )

    def test_only_dispatcher_can_include_version_text(self):
        result = self.versions(actor=self.dispatcher, include_text=True, member="alice")
        self.assertEqual(
            [item["text"] for item in result["versions"]],
            ["second version", "first version"],
        )
        with self.assertRaisesRegex(ValueError, "forbidden"):
            self.versions(actor=self.alice, include_text=True)
        self.assertEqual(len(self.audit_details()), 1)
        self.assertIn('"what": "include_text"', self.audit_details()[0]["detail"])

    def test_invalid_scope_and_key_are_rejected(self):
        for scope, key, expected in (
            ("unknown", "", "invalid_argument"),
            ("private", "Bad Key", "invalid_key"),
            ("private-core", "extra", "invalid_key"),
        ):
            with self.assertRaisesRegex(ValueError, expected):
                self.read(scope=scope, key=key)


if __name__ == "__main__":
    unittest.main(verbosity=2)
