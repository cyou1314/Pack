#!/usr/bin/env python3
"""Attachment read, file integrity and external isolation regression."""
import base64
import hashlib
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.attachments import get_attachment, list_attachments


class AttachmentReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-attach-read-")
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
            "INSERT INTO rooms(name,created_at) VALUES('other-room',11000)"
        )
        self.db.execute(
            "INSERT INTO members(name,role,token_hash,created_at,kind,"
            "bound_room_id,joined_msg_id) "
            "VALUES('guest','member','hash',10000,'external',1,0)"
        )
        self.db.commit()
        self.guest = dict(self.db.execute(
            "SELECT * FROM members WHERE name='guest'"
        ).fetchone())
        self.other_room_id = self.db.execute(
            "SELECT id FROM rooms WHERE name='other-room'"
        ).fetchone()[0]
        self.root = root

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def add_attachment(self, *, data=bytes.fromhex("89504e470d0a1a0a") + b"real-image",
                       room_id=1, created_at=11999, message_id=None):
        sha = hashlib.sha256(data).hexdigest()
        path = Path(hub._attach_path(sha, "image/png", self.context))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        cur = self.db.execute(
            "INSERT INTO attachments(sha256,mime,size,original_name,"
            "uploader_id,room_id,message_id,created_at)"
            " VALUES(?,'image/png',?,'photo.png',?,?,?,?)",
            (sha, len(data), self.dispatcher["id"], room_id,
             message_id, created_at),
        )
        self.db.commit()
        return cur.lastrowid, path

    def read(self, attachment_id, member=None):
        return get_attachment(
            self.context, member or self.dispatcher, attachment_id,
            attach_path=lambda sha, mime: hub._attach_path(sha, mime, self.context),
            max_bytes=hub.ATTACH_MAX_BYTES,
        )

    def listing(self, room="pack-main", message_id=0, member=None):
        return list_attachments(
            self.context, member or self.dispatcher, room, message_id,
        )["attachments"]

    def test_read_returns_original_image_with_audit(self):
        attachment_id, _ = self.add_attachment()
        result = self.read(attachment_id)
        self.assertEqual(result["attachment_id"], attachment_id)
        self.assertEqual(result["mime"], "image/png")
        self.assertEqual(result["filename"], "photo.png")
        self.assertEqual(
            base64.b64decode(result["data_base64"]),
            bytes.fromhex("89504e470d0a1a0a") + b"real-image",
        )
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM audit WHERE action='attachment_get'"
        ).fetchone()[0], 1)

    def test_external_cannot_read_other_room_or_prejoin_attachment(self):
        old, _ = self.add_attachment(data=b"old", created_at=9000)
        other, _ = self.add_attachment(
            data=b"outside", room_id=self.other_room_id,
        )
        recent, _ = self.add_attachment(data=b"allowed", created_at=11000)
        with self.assertRaisesRegex(ValueError, "predates"):
            self.read(old, self.guest)
        with self.assertRaisesRegex(ValueError, "outside the bound room"):
            self.read(other, self.guest)
        self.assertEqual(self.read(recent, self.guest)["attachment_id"], recent)

    def test_missing_attachment_and_file(self):
        with self.assertRaisesRegex(ValueError, "no such attachment"):
            self.read(999)
        attachment_id, path = self.add_attachment()
        path.unlink()
        with self.assertRaisesRegex(ValueError, "file missing"):
            self.read(attachment_id)

    def test_corrupt_file_and_invalid_size_are_rejected(self):
        attachment_id, path = self.add_attachment()
        path.write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.read(attachment_id)
        self.db.execute(
            "UPDATE attachments SET size=? WHERE id=?",
            (hub.ATTACH_MAX_BYTES + 1, attachment_id),
        )
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.read(attachment_id)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM audit WHERE action='attachment_get'"
        ).fetchone()[0], 0)

    def test_symlink_cannot_escape_attachment_storage(self):
        attachment_id, path = self.add_attachment()
        outside = self.root / "private.txt"
        outside.write_text("secret", encoding="utf-8")
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.read(attachment_id)

    def test_audit_failure_does_not_record_success(self):
        attachment_id, _ = self.add_attachment()
        self.db.execute(
            "CREATE TRIGGER reject_get BEFORE INSERT ON audit "
            "WHEN NEW.action='attachment_get' "
            "BEGIN SELECT RAISE(ABORT,'get audit rejected'); END"
        )
        self.db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "get audit rejected"):
            self.read(attachment_id)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM audit WHERE action='attachment_get'"
        ).fetchone()[0], 0)

    def test_list_scopes_room_and_external_join(self):
        old, _ = self.add_attachment(data=b"one", created_at=9000)
        recent, _ = self.add_attachment(data=b"two", created_at=11000)
        other, _ = self.add_attachment(
            data=b"three", room_id=self.other_room_id,
        )
        self.assertEqual(
            [x["id"] for x in self.listing()], [old, recent]
        )
        self.assertEqual(
            [x["id"] for x in self.listing(member=self.guest)], [recent]
        )
        self.assertEqual(
            [x["id"] for x in self.listing(room="other-room")], [other]
        )
        with self.assertRaisesRegex(ValueError, "bound room"):
            self.listing(room="other-room", member=self.guest)
        with self.assertRaisesRegex(ValueError, "no such room"):
            self.listing(room="nonexistent")

    def test_list_message_filter_matches_only_that_message(self):
        message = self.db.execute(
            "INSERT INTO messages(room_id,author_id,kind,body,created_at)"
            " VALUES(1,?,'chat','hello',11999)",
            (self.dispatcher["id"],),
        ).lastrowid
        self.db.commit()
        linked, _ = self.add_attachment(data=b"linked", message_id=message)
        self.add_attachment(data=b"unlinked")
        self.assertEqual(
            [x["id"] for x in self.listing(message_id=message)], [linked]
        )
        self.assertEqual(self.listing(message_id=999999), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
