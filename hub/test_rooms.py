#!/usr/bin/env python3
"""Room read service compatibility tests using disposable SQLite fixtures."""
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.rooms import read_new_messages


class RoomReadServiceTests(unittest.TestCase):
    def test_external_floor_tail_and_deleted_payload_redaction(self):
        with tempfile.TemporaryDirectory(prefix="pack-room-read-") as directory:
            root = Path(directory)
            context = HubContext(
                base_dir=str(root),
                db_path=str(root / "isolated.db"),
                attachment_dir=str(root / "attachments"),
                lock=threading.Lock(),
                clock=lambda: 1000,
            )
            hub.init(context)
            conn = context.connect()
            try:
                author = conn.execute(
                    "SELECT id FROM members WHERE role='dispatcher'"
                ).fetchone()[0]
                for idx in range(1, 5):
                    conn.execute(
                        "INSERT INTO messages(room_id,author_id,kind,body,created_at,"
                        "deleted_at,payload,reply_to_id) VALUES(?,?,?,?,?,?,?,?)",
                        (1, author, "chat", f"msg{idx}", 1000 + idx,
                         1000 if idx == 3 else None,
                         '{"text":"private"}', 1),
                    )
                conn.commit()
            finally:
                conn.close()

            member = {"name": "tester", "kind": "member"}
            all_messages = read_new_messages(context, member, "pack-main")
            self.assertEqual([x["id"] for x in all_messages["messages"]], [1, 2, 3, 4])
            self.assertEqual(all_messages["messages"][2]["body"], "（已删除）")
            self.assertIsNone(all_messages["messages"][2]["payload"])
            self.assertIsNone(all_messages["messages"][2]["reply_to_id"])

            newest = read_new_messages(context, member, "pack-main", 0, 2, True)
            self.assertEqual([x["id"] for x in newest["messages"]], [3, 4])

            external = {
                "name": "guest", "kind": "external",
                "bound_room_id": 1, "joined_msg_id": 2,
            }
            restricted = read_new_messages(context, external, "pack-main")
            self.assertEqual([x["id"] for x in restricted["messages"]], [3, 4])
            with self.assertRaisesRegex(ValueError, "bound room"):
                read_new_messages(context, {
                    **external, "bound_room_id": 999,
                }, "pack-main")
            with self.assertRaisesRegex(ValueError, "no such room"):
                read_new_messages(context, member, "missing")

    def test_limit_clamping(self):
        with tempfile.TemporaryDirectory(prefix="pack-room-limit-") as directory:
            root = Path(directory)
            context = HubContext(
                base_dir=str(root), db_path=str(root / "isolated.db"),
                attachment_dir=str(root / "attachments"),
                lock=threading.Lock(), clock=lambda: 1000,
            )
            hub.init(context)
            self.assertEqual(
                read_new_messages(context, {"kind": "member"}, "pack-main", limit=0),
                {"messages": []},
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
