#!/usr/bin/env python3
"""Isolated inbox pull/mark-read regression with SQLite concurrency checks."""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.messages import pull_inbox_messages, mark_inbox_read


class InboxReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-inbox-read-")
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
        for name, kind in (("worker", None), ("guest", "external")):
            self.db.execute(
                "INSERT INTO members(name,role,token_hash,created_at,kind,"
                "bound_room_id,joined_msg_id) VALUES(?,'member',?,11000,?,?,0)",
                (name, name+"-hash", kind, 1 if kind == "external" else None),
            )
        self.db.execute(
            "INSERT INTO rooms(name,created_at) VALUES('other-room',11000)"
        )
        self.db.commit()
        self.worker = self.member("worker")
        self.guest = self.member("guest")

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def member(self, name):
        return dict(self.db.execute(
            "SELECT * FROM members WHERE name=?", (name,),
        ).fetchone())

    def seed(self, author=None, body="message", *, room_id=1, ts=11999):
        author = author or self.worker
        cur = self.db.execute(
            "INSERT INTO messages(room_id,author_id,kind,body,created_at)"
            " VALUES(?,?,'chat',?,?)",
            (room_id, author["id"], body, ts),
        )
        self.db.commit()
        return cur.lastrowid

    def resolve(self, member, room):
        return hub._inbox_resolve_room(member, room, self.context)

    def pull(self, member=None, *, since_id=-1, room="pack-main"):
        return pull_inbox_messages(
            self.context, member or self.dispatcher, since_id, room,
            user_agent="pack-agent/1.0.0",
            resolve_room=self.resolve,
            read_cursor=hub._read_cursor,
            inbox_msg_sql=hub._INBOX_MSG_SQL,
            inbox_items=hub._inbox_items,
            window_seconds=hub.INBOX_WINDOW_SECONDS,
            pull_limit=hub.INBOX_PULL_LIMIT,
            checkin_audit_gap=hub.INBOX_CHECKIN_AUDIT_GAP,
        )

    def mark(self, up_to_id=0, member=None, *, room="pack-main"):
        return mark_inbox_read(
            self.context, member or self.dispatcher, up_to_id, room,
            resolve_room=self.resolve, read_cursor=hub._read_cursor,
        )

    def cursor(self, member=None):
        member = member or self.dispatcher
        row = self.db.execute(
            "SELECT cursor_id FROM member_read_cursors"
            " WHERE member_id=? AND room_id=1", (member["id"],),
        ).fetchone()
        return row[0] if row else None

    def test_pull_checkin_formats_messages_without_marking_read(self):
        mid = self.seed(body="@dispatcher investigate")
        result = self.pull()
        self.assertEqual(result["room"], "pack-main")
        self.assertEqual(result["cursor"], 0)
        self.assertFalse(result["has_more"])
        self.assertEqual([x["id"] for x in result["messages"]], [mid])
        self.assertTrue(result["messages"][0]["mentions_me"])
        self.assertEqual(result["messages"][0]["author_role"], "member")
        self.assertIsNone(self.cursor())
        self.assertEqual(self.db.execute(
            "SELECT last_heartbeat FROM members WHERE id=?",
            (self.dispatcher["id"],),
        ).fetchone()[0], 12000.0)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM audit WHERE action='inbox_pull'",
        ).fetchone()[0], 1)

    def test_explicit_since_replays_without_rewinding_server_cursor(self):
        first = self.seed(body="one")
        second = self.seed(body="two")
        self.assertEqual(self.mark(first)["cursor"], first)
        replay = self.pull(since_id=0)
        self.assertEqual([x["id"] for x in replay["messages"]], [first, second])
        self.assertEqual(replay["cursor"], first)
        normal = self.pull()
        self.assertEqual([x["id"] for x in normal["messages"]], [second])
        self.assertEqual(self.cursor(), first)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM audit WHERE action='inbox_checkin_frequent'",
        ).fetchone()[0], 1)

    def test_external_join_watermark_and_bound_room_enforced(self):
        old = self.seed(body="before join")
        recent = self.seed(body="after join")
        self.db.execute(
            "UPDATE members SET joined_msg_id=? WHERE id=?",
            (old, self.guest["id"]),
        )
        self.db.commit()
        guest = self.member("guest")
        result = self.pull(guest, since_id=0, room="")
        self.assertEqual(result["cursor"], old)
        self.assertEqual([x["id"] for x in result["messages"]], [recent])
        self.assertEqual(self.mark(0, guest, room="")["cursor"], recent)
        with self.assertRaisesRegex(ValueError, "bound room"):
            self.pull(guest, room="other-room")

    def test_window_limit_and_has_more(self):
        old = self.seed(ts=1)
        for i in range(hub.INBOX_PULL_LIMIT + 1):
            self.seed(body=f"recent-{i}")
        result = self.pull()
        self.assertTrue(result["has_more"])
        self.assertEqual(len(result["messages"]), hub.INBOX_PULL_LIMIT)
        self.assertNotIn(old, [x["id"] for x in result["messages"]])

    def test_mark_read_is_monotonic_and_clamped_to_room_max(self):
        first = self.seed()
        second = self.seed()
        third = self.seed()
        self.assertEqual(self.mark(second)["cursor"], second)
        self.assertEqual(self.mark(first)["cursor"], second)
        self.assertEqual(self.mark(999999)["cursor"], third)
        self.assertEqual(self.mark(-2)["cursor"], third)
        self.assertEqual(self.cursor(), third)

    def test_pull_audit_failure_rolls_back_heartbeat(self):
        old_heartbeat = self.db.execute(
            "SELECT last_heartbeat FROM members WHERE id=?",
            (self.dispatcher["id"],),
        ).fetchone()[0]
        self.db.execute(
            "CREATE TRIGGER reject_pull BEFORE INSERT ON audit "
            "WHEN NEW.action='inbox_pull' BEGIN "
            "SELECT RAISE(ABORT,'pull audit rejected'); END"
        )
        self.db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "pull audit rejected"):
            self.pull()
        self.assertEqual(self.db.execute(
            "SELECT last_heartbeat FROM members WHERE id=?",
            (self.dispatcher["id"],),
        ).fetchone()[0], old_heartbeat)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM audit WHERE action='inbox_pull'",
        ).fetchone()[0], 0)

    def test_mark_audit_failure_rolls_back_cursor(self):
        mid = self.seed()
        self.db.execute(
            "CREATE TRIGGER reject_mark BEFORE INSERT ON audit "
            "WHEN NEW.action='inbox_mark_read' BEGIN "
            "SELECT RAISE(ABORT,'mark audit rejected'); END"
        )
        self.db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "mark audit rejected"):
            self.mark(mid)
        self.assertIsNone(self.cursor())

    def test_parallel_mark_read_cannot_rewind_cursor(self):
        first = self.seed()
        second = self.seed()
        errors = []
        barrier = threading.Barrier(2)

        def run(target):
            try:
                barrier.wait(timeout=5)
                self.mark(target)
            except Exception as error:
                errors.append(error)

        threads = [
            threading.Thread(target=run, args=(first,)),
            threading.Thread(target=run, args=(second,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(self.cursor(), second)


if __name__ == "__main__":
    unittest.main(verbosity=2)
