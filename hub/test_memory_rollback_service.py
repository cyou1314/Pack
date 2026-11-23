#!/usr/bin/env python3
"""Memory rollback service authorization, provenance and transaction tests."""
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.memory import rollback_memory


class MemoryRollbackServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-memory-rollback-")
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
        self.v1, _, _ = hub._mem_append_version(
            self.db, "alice", "private", "notes", "first",
            "alice", "self", [], self.context,
        )
        self.v2, _, _ = hub._mem_append_version(
            self.db, "alice", "private", "notes", "second",
            "alice", "external", [], self.context,
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def rollback(self, actor=None, scope="private", key="notes",
                 version_id=None, member=""):
        return rollback_memory(
            self.context, actor or self.dispatcher, scope, key,
            self.v1 if version_id is None else version_id, member,
            validate_scope_key=hub._mem_validate_scope_key,
            resolve_target=lambda conn, who, layer, target: hub._mem_target(
                conn, who, layer, target, context=self.context,
            ),
            denied=lambda conn, who, target, layer, what: hub._mem_denied(
                conn, who, target, layer, what, context=self.context,
            ),
            error=hub._mem_err,
            append_version=lambda conn, target, layer, slug, body, author, origin, refs:
                hub._mem_append_version(
                    conn, target, layer, slug, body, author, origin, refs,
                    self.context,
                ),
        )

    def current(self, member="alice", scope="private", key="notes"):
        return self.db.execute(
            "SELECT text,version_id FROM member_memory "
            "WHERE member=? AND scope=? AND key=?",
            (member, scope, key),
        ).fetchone()

    def count(self, table, where="", args=()):
        return self.db.execute(
            f"SELECT COUNT(*) FROM {table} {where}", args,
        ).fetchone()[0]

    def test_dispatcher_rollback_appends_new_version_with_provenance(self):
        result = self.rollback()
        self.assertEqual(result, {
            "new_version_id": 3, "restored_from": self.v1,
        })
        self.assertEqual(tuple(self.current()), ("first", 3))
        row = self.db.execute(
            "SELECT text,author,origin,source_refs FROM member_memory_versions "
            "WHERE id=?", (result["new_version_id"],),
        ).fetchone()
        self.assertEqual(row["text"], "first")
        self.assertEqual(row["author"], "dispatcher")
        self.assertEqual(row["origin"], "dispatcher")
        self.assertEqual(
            json.loads(row["source_refs"]),
            [{"type": "rollback_of", "id": self.v1}],
        )

    def test_member_rollback_is_denied_and_audited(self):
        with self.assertRaisesRegex(ValueError, "forbidden"):
            self.rollback(actor=self.alice)
        self.assertEqual(self.count("member_memory_versions"), 2)
        row = self.db.execute(
            "SELECT detail FROM audit WHERE action='memory_access_denied'"
        ).fetchone()
        self.assertIn('"what": "rollback"', row["detail"])

    def test_wrong_member_scope_or_key_version_is_not_found(self):
        with self.assertRaisesRegex(ValueError, "not_found"):
            self.rollback(member="bob")
        with self.assertRaisesRegex(ValueError, "not_found"):
            self.rollback(key="other-key")
        self.assertEqual(self.count("member_memory_versions"), 2)

    def test_room_shared_target_ignores_member_override(self):
        shared_id, _, _ = hub._mem_append_version(
            self.db, hub.MEM_ROOM_MEMBER, "room-shared", "",
            "shared", "dispatcher", "dispatcher", [], self.context,
        )
        self.db.commit()
        result = self.rollback(
            scope="room-shared", key="", version_id=shared_id, member="alice",
        )
        self.assertEqual(self.current(
            member=hub.MEM_ROOM_MEMBER, scope="room-shared", key="",
        )[0], "shared")
        self.assertEqual(result["restored_from"], shared_id)

    def test_audit_failure_rolls_back_new_version_and_current_value(self):
        self.db.execute(
            "CREATE TRIGGER reject_memory_rollback BEFORE INSERT ON audit "
            "WHEN NEW.action='memory_rollback' BEGIN "
            "SELECT RAISE(ABORT,'memory rollback audit rejected'); END"
        )
        self.db.commit()
        before = tuple(self.current())
        with self.assertRaisesRegex(sqlite3.IntegrityError, "memory rollback audit rejected"):
            self.rollback()
        self.assertEqual(tuple(self.current()), before)
        self.assertEqual(self.count("member_memory_versions"), 2)
        self.assertEqual(self.count("audit", "WHERE action='memory_rollback'"), 0)

    def test_invalid_scope_key_is_rejected_without_write(self):
        with self.assertRaisesRegex(ValueError, "invalid_key"):
            self.rollback(key="bad key")
        self.assertEqual(self.count("member_memory_versions"), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
