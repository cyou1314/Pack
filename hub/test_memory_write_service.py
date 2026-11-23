#!/usr/bin/env python3
"""Memory write transaction, quota, versioning and cross-process tests."""
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.memory import write_memory


class MemoryWriteServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-memory-write-")
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
        self.db.commit()
        self.alice = dict(self.db.execute(
            "SELECT * FROM members WHERE name='alice'"
        ).fetchone())

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def write(self, context=None, actor=None, scope="private", key="notes",
              text="memory text", source_refs=None, origin="self", member=""):
        ctx = context or self.context
        return write_memory(
            ctx, actor or self.alice, scope, key, text,
            source_refs, origin, member,
            validate_scope_key=hub._mem_validate_scope_key,
            resolve_target=lambda conn, who, layer, target: hub._mem_target(
                conn, who, layer, target, context=ctx,
            ),
            denied=lambda conn, who, target, layer, what: hub._mem_denied(
                conn, who, target, layer, what, context=ctx,
            ),
            normalize_origin=hub._mem_origin,
            validate_refs=hub._mem_validate_refs,
            error=hub._mem_err,
            measure=hub._mem_measure,
            current_value=hub._mem_current,
            append_version=lambda conn, target, layer, slug, body, author, source, refs:
                hub._mem_append_version(
                    conn, target, layer, slug, body, author, source, refs, ctx,
                ),
            core_limit_bytes=hub.MEM_CORE_LIMIT_BYTES,
            shared_limit_bytes=hub.MEM_SHARED_LIMIT_BYTES,
            topic_limit_chars=hub.MEM_L1_LIMIT_CHARS,
            max_topics=hub.MEM_L1_MAX_TOPICS,
            soft_cap_chars=hub.MEM_L1_SOFT_CAP_CHARS,
            limit_hint=hub._MEM_HINT_LIMIT,
        )

    def count(self, table, where="", args=()):
        return self.db.execute(
            f"SELECT COUNT(*) FROM {table} {where}", args,
        ).fetchone()[0]

    def current(self, scope="private", key="notes", member="alice"):
        return self.db.execute(
            "SELECT text,version_id FROM member_memory "
            "WHERE member=? AND scope=? AND key=?",
            (member, scope, key),
        ).fetchone()

    def test_write_appends_version_updates_current_and_audits_metadata(self):
        result = self.write(source_refs=[{"type": "task", "id": 9}], origin="external")
        self.assertEqual(result, {
            "version_id": 1, "size": len("memory text"),
            "limit": hub.MEM_L1_LIMIT_CHARS,
        })
        self.assertEqual(tuple(self.current()), ("memory text", 1))
        version = self.db.execute(
            "SELECT author,origin,source_refs FROM member_memory_versions WHERE id=1"
        ).fetchone()
        self.assertEqual(version["author"], "alice")
        self.assertEqual(version["origin"], "external")
        self.assertEqual(json.loads(version["source_refs"]), [{"type": "task", "id": 9}])
        audit = self.db.execute(
            "SELECT actor,detail FROM audit WHERE action='memory_write'"
        ).fetchone()
        self.assertEqual(audit["actor"], "alice")
        self.assertNotIn("memory text", audit["detail"])

    def test_dispatcher_origin_is_forced_by_server(self):
        self.write(actor=self.dispatcher, origin="external")
        self.assertEqual(self.db.execute(
            "SELECT origin FROM member_memory_versions"
        ).fetchone()[0], "dispatcher")

    def test_member_cannot_self_report_dispatcher_origin(self):
        with self.assertRaisesRegex(ValueError, "origin=dispatcher"):
            self.write(origin="dispatcher")
        self.assertEqual(self.count("member_memory_versions"), 0)

    def test_invalid_source_refs_and_non_string_text_are_rejected(self):
        for refs in ("not-a-list", [{"type": "unknown"}], [{"type": "task", "id": True}]):
            with self.assertRaisesRegex(ValueError, "source_refs"):
                self.write(source_refs=refs)
        with self.assertRaisesRegex(ValueError, "text 必须是字符串"):
            self.write(text=123)
        self.assertEqual(self.count("member_memory_versions"), 0)

    def test_private_core_uses_utf8_byte_limit(self):
        with self.assertRaises(ValueError) as error:
            self.write(scope="private-core", key="", text="你" * 1366)
        payload = json.loads(str(error.exception))
        self.assertEqual(payload["error"], "memory_limit_exceeded")
        self.assertEqual(payload["current_bytes"], len(("你" * 1366).encode("utf-8")))
        self.assertEqual(self.count("member_memory_versions"), 0)

    def test_private_topic_character_limit(self):
        with self.assertRaises(ValueError) as error:
            self.write(text="x" * (hub.MEM_L1_LIMIT_CHARS + 1))
        self.assertEqual(json.loads(str(error.exception))["error"], "memory_limit_exceeded")
        self.assertEqual(self.count("member_memory_versions"), 0)

    def test_room_shared_write_is_dispatcher_only_and_audited(self):
        with self.assertRaisesRegex(ValueError, "forbidden"):
            self.write(scope="room-shared", key="")
        self.assertEqual(self.count("member_memory_versions"), 0)
        row = self.db.execute(
            "SELECT detail FROM audit WHERE action='memory_access_denied'"
        ).fetchone()
        self.assertIn("write-room-shared", row["detail"])

    def test_delete_private_topic_writes_tombstone_and_frees_slot(self):
        self.write()
        result = self.write(text=None, source_refs=[{"type": "message", "id": 5}])
        self.assertTrue(result["deleted"])
        self.assertEqual(result["size"], 0)
        self.assertEqual(self.current()[0], "")
        version = self.db.execute(
            "SELECT text,source_refs FROM member_memory_versions WHERE id=?",
            (result["version_id"],),
        ).fetchone()
        self.assertEqual(version["text"], "")
        self.assertEqual(
            json.loads(version["source_refs"]),
            [{"type": "message", "id": 5}, {"type": "delete"}],
        )

    def test_delete_is_restricted_to_private_scope(self):
        with self.assertRaisesRegex(ValueError, "text=null"):
            self.write(scope="private-core", key="", text=None)
        self.assertEqual(self.count("member_memory_versions"), 0)

    def test_soft_cap_is_warning_only_and_audited(self):
        for index in range(10):
            self.write(key=f"topic-{index}", text="x" * 20000)
        result = self.write(key="topic-10", text="y")
        self.assertIsNotNone(result["version_id"])
        row = self.db.execute(
            "SELECT detail FROM audit WHERE action='memory_soft_cap'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertIn('"soft_cap": 200000', row["detail"])

    def test_audit_failure_rolls_back_version_and_current_value(self):
        self.db.execute(
            "CREATE TRIGGER reject_memory_audit BEFORE INSERT ON audit "
            "WHEN NEW.action='memory_write' BEGIN "
            "SELECT RAISE(ABORT,'memory audit rejected'); END"
        )
        self.db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "memory audit rejected"):
            self.write()
        self.assertEqual(self.count("member_memory_versions"), 0)
        self.assertIsNone(self.current())

    def test_two_hub_contexts_cannot_exceed_private_topic_limit(self):
        for index in range(hub.MEM_L1_MAX_TOPICS - 1):
            self.write(key=f"seed-{index}", text="seed")
        self.db.commit()
        root = Path(self.temp.name)
        independent = HubContext(
            base_dir=str(root), db_path=str(root / "hub.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(), clock=lambda: 12500.0,
        )
        warm = independent.connect()
        warm.close()
        barrier = threading.Barrier(2)
        successes, errors = [], []

        def submit(context, key):
            try:
                barrier.wait(timeout=10)
                successes.append(self.write(
                    context=context, key=key, text="new topic",
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
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(json.loads(str(errors[0]))["error"], "memory_topic_limit")
        self.assertEqual(self.count(
            "member_memory", "WHERE member='alice' AND scope='private' AND text!=''",
        ), hub.MEM_L1_MAX_TOPICS)
        self.assertEqual(self.count("member_memory_versions"), hub.MEM_L1_MAX_TOPICS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
