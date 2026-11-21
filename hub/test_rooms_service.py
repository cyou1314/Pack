#!/usr/bin/env python3
"""Contract tests for the extracted room read services."""
import json
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from services import rooms
from core.context import HubContext
from services.rooms import get_room_summary, list_rooms


class RoomServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pack-rooms-test-")
        root = Path(self.temporary.name)
        self.context = HubContext(
            base_dir=str(root),
            db_path=str(root / "rooms.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(),
            clock=lambda: 5000.0,
        )
        hub.init(self.context)
        connection = self.context.connect()
        try:
            dispatcher_id = connection.execute(
                "SELECT id FROM members WHERE role='dispatcher'"
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO rooms(name,created_at,archived_at) VALUES('active-room',5000,NULL)"
            )
            connection.execute(
                "INSERT INTO rooms(name,created_at,archived_at) VALUES('archived-room',5000,5000)"
            )
            active_id = connection.execute(
                "SELECT id FROM rooms WHERE name='active-room'"
            ).fetchone()[0]
            archived_id = connection.execute(
                "SELECT id FROM rooms WHERE name='archived-room'"
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO messages(room_id,author_id,kind,body,created_at) "
                "VALUES(?,?,'chat','latest active message',5001)",
                (active_id, dispatcher_id),
            )
            connection.execute(
                "INSERT INTO room_summaries(room_id,data,updated_at,updated_by) "
                "VALUES(?,?,?,?)",
                (active_id, json.dumps({"decided":["one"],"in_progress":[],"pending":[]}), 5002, "dispatcher"),
            )
            connection.commit()
            self.active_id = active_id
            self.archived_id = archived_id
        finally:
            connection.close()

    def tearDown(self):
        self.temporary.cleanup()

    def test_list_rooms_respects_archive_filter_and_last_message_cursor(self):
        result = list_rooms(self.context, {"kind":"internal"})
        names = [room["name"] for room in result["rooms"]]
        self.assertEqual(names, ["pack-main", "active-room"])
        active = next(room for room in result["rooms"] if room["name"]=="active-room")
        self.assertEqual(active["last_message_id"], 1)
        self.assertFalse(active["archived"])

        all_rooms = list_rooms(self.context, {"kind":"internal"}, include_archived=True)
        self.assertEqual(
            [room["name"] for room in all_rooms["rooms"]],
            ["pack-main", "active-room", "archived-room"],
        )

    def test_external_room_list_is_limited_to_bound_room(self):
        result = list_rooms(
            self.context,
            {"kind":"external","bound_room_id":self.active_id},
            include_archived=True,
        )
        self.assertEqual([room["id"] for room in result["rooms"]], [self.active_id])

    def test_room_summary_reads_saved_value_and_empty_default(self):
        saved = get_room_summary(
            self.context,
            {"kind":"internal"},
            "active-room",
            hub.SUMMARY_SECTIONS,
        )
        self.assertEqual(saved["summary"]["decided"], ["one"])
        self.assertEqual(saved["updated_by"], "dispatcher")

        empty = get_room_summary(
            self.context,
            {"kind":"internal"},
            "pack-main",
            hub.SUMMARY_SECTIONS,
        )
        self.assertEqual(empty["summary"], {"decided": [], "in_progress": [], "pending": []})
        self.assertIsNone(empty["updated_at"])

    def test_external_member_cannot_read_unbound_room_summary(self):
        with self.assertRaisesRegex(ValueError, "bound room"):
            get_room_summary(
                self.context,
                {"kind":"external","bound_room_id":self.active_id},
                "archived-room",
                hub.SUMMARY_SECTIONS,
            )

    def test_room_write_services_create_rename_archive_and_summary(self):
        created = rooms.create_room(self.context, {"name":"dispatcher"}, "work-room")
        self.assertEqual(created["name"], "work-room")
        with self.assertRaisesRegex(ValueError, "already exists"):
            rooms.create_room(self.context, {"name":"dispatcher"}, "work-room")
        renamed = rooms.rename_room(
            self.context, {"name":"dispatcher"}, "work-room", "delivery-room"
        )
        self.assertEqual(renamed["name"], "delivery-room")
        with self.assertRaisesRegex(ValueError, "main room"):
            rooms.rename_room(
                self.context, {"name":"dispatcher"}, "pack-main", "renamed-main"
            )

        archived = rooms.archive_room(
            self.context, {"name":"dispatcher"}, "delivery-room", True
        )
        self.assertTrue(archived["archived"])
        restored = rooms.archive_room(
            self.context, {"name":"dispatcher"}, "delivery-room", False
        )
        self.assertFalse(restored["archived"])
        with self.assertRaisesRegex(ValueError, "main room"):
            rooms.archive_room(self.context, {"name":"dispatcher"}, "pack-main", True)

        summary = json.dumps(
            {"decided":["done"],"in_progress":[],"pending":["next"]},
            ensure_ascii=False,
        )
        saved = rooms.set_room_summary(
            self.context,
            {"name":"dispatcher"},
            "delivery-room",
            summary,
            hub._validate_summary,
        )
        self.assertEqual(saved["summary"]["decided"], ["done"])
        with self.assertRaises(ValueError):
            rooms.set_room_summary(
                self.context,
                {"name":"dispatcher"},
                "delivery-room",
                "{invalid json",
                hub._validate_summary,
            )
        reread = rooms.get_room_summary(
            self.context, {"kind":"internal"}, "delivery-room", hub.SUMMARY_SECTIONS
        )
        self.assertEqual(reread["summary"]["decided"], ["done"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
