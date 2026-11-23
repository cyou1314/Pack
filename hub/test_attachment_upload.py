#!/usr/bin/env python3
"""Atomic attachment upload and cross-connection quota regression tests."""
import base64
import hashlib
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.attachments import get_attachment, put_attachment

PNG = bytes.fromhex("89504e470d0a1a0a") + b"image-payload"


class AttachmentUploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-attach-upload-")
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
        self.root = root

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def put(self, data=PNG, member=None, room="pack-main", *,
            room_quota=500_000_000, daily_uploads=10,
            member_quota=50_000_000, external_max=5_000_000,
            context=None):
        ctx = context or self.context
        return put_attachment(
            ctx, member or self.dispatcher, room, "sub/filename.png",
            "image/png", base64.b64encode(data).decode(),
            mime_extensions=hub.ATTACH_MIME_EXT,
            max_bytes=hub.ATTACH_MAX_BYTES, room_quota=room_quota,
            external_max_bytes=external_max,
            external_daily_uploads=daily_uploads,
            external_member_quota=member_quota,
            sniff_image_mime=hub._sniff_image_mime,
            attach_path=lambda sha, mime: hub._attach_path(sha, mime, ctx),
        )

    def count(self, table):
        return self.db.execute("SELECT COUNT(*) FROM "+table).fetchone()[0]

    def file(self, data=PNG):
        sha = hashlib.sha256(data).hexdigest()
        return Path(hub._attach_path(sha, "image/png", self.context))

    def test_upload_and_read_roundtrip_preserves_metadata_and_permissions(self):
        created = self.put()
        self.assertEqual(created["sha256"], hashlib.sha256(PNG).hexdigest())
        self.assertEqual(self.count("attachments"), 1)
        stored = self.file()
        self.assertEqual(stored.read_bytes(), PNG)
        self.assertEqual(stored.stat().st_mode & 0o777, 0o600)
        row = self.db.execute(
            "SELECT original_name,message_id FROM attachments"
        ).fetchone()
        self.assertEqual(tuple(row), ("sub_filename.png", None))
        downloaded = get_attachment(
            self.context, self.dispatcher, created["attachment_id"],
            attach_path=lambda sha, mime: hub._attach_path(
                sha, mime, self.context
            ), max_bytes=hub.ATTACH_MAX_BYTES,
        )
        self.assertEqual(base64.b64decode(downloaded["data_base64"]), PNG)
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM audit WHERE action='attachment_put'"
            ).fetchone()[0], 1
        )

    def test_same_hash_reuses_file_but_creates_distinct_metadata_rows(self):
        first = self.put()
        second = self.put()
        self.assertNotEqual(first["attachment_id"], second["attachment_id"])
        self.assertEqual(self.count("attachments"), 2)
        self.assertEqual(self.file().read_bytes(), PNG)
        self.assertEqual(len(list(self.file().parent.glob(".pack-upload-*"))), 0)

    def test_input_validation_rejects_malformed_data_before_writing(self):
        with self.assertRaisesRegex(ValueError, "invalid base64"):
            put_attachment(
                self.context, self.dispatcher, "pack-main", "x.png",
                "image/png", "bad!",
                mime_extensions=hub.ATTACH_MIME_EXT,
                max_bytes=hub.ATTACH_MAX_BYTES,
                room_quota=hub.ATTACH_ROOM_QUOTA,
                external_max_bytes=hub.EXT_ATTACH_MAX_BYTES,
                external_daily_uploads=hub.EXT_ATTACH_DAILY_UPLOADS,
                external_member_quota=hub.EXT_ATTACH_MEMBER_QUOTA,
                sniff_image_mime=hub._sniff_image_mime,
                attach_path=lambda sha, mime: hub._attach_path(
                    sha, mime, self.context
                ),
            )
        with self.assertRaisesRegex(ValueError, "mime does not match"):
            self.put(b"not-an-image")
        self.assertEqual(self.count("attachments"), 0)

    def test_external_room_binding_and_single_file_limit_audit(self):
        with self.assertRaisesRegex(ValueError, "bound room"):
            self.put(member=self.guest, room="other-room")
        with self.assertRaisesRegex(ValueError, "too large for external"):
            self.put(member=self.guest, external_max=8)
        self.assertEqual(self.count("attachments"), 0)
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM audit "
                "WHERE action='external_attachment_quota'"
            ).fetchone()[0], 1
        )

    def test_external_daily_and_total_caps_audited_without_file_creation(self):
        for i in range(3):
            self.db.execute(
                "INSERT INTO attachments(sha256,mime,size,original_name,"
                "uploader_id,room_id,created_at) "
                "VALUES(?,'image/png',1,'prior',?,1,11999)",
                (f"prior-{i}", self.guest["id"]),
            )
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "daily upload cap"):
            self.put(member=self.guest, daily_uploads=3)
        with self.assertRaisesRegex(ValueError, "member attachment quota"):
            self.put(member=self.guest, daily_uploads=10, member_quota=3)
        self.assertEqual(self.count("attachments"), 3)
        self.assertFalse(self.file().exists())
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM audit WHERE action='external_attachment_quota'"
        ).fetchone()[0], 2)

    def test_room_cap_rejection_works_for_regular_members(self):
        with self.assertRaisesRegex(ValueError, "room attachment quota"):
            self.put(room_quota=len(PNG)-1)
        self.assertEqual(self.count("attachments"), 0)
        self.assertFalse(self.file().exists())

    def test_audit_failure_rolls_back_metadata_and_new_file(self):
        self.db.execute(
            "CREATE TRIGGER reject_upload BEFORE INSERT ON audit "
            "WHEN NEW.action='attachment_put' BEGIN "
            "SELECT RAISE(ABORT,'upload audit rejected'); END"
        )
        self.db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "upload audit rejected"):
            self.put()
        self.assertEqual(self.count("attachments"), 0)
        self.assertFalse(self.file().exists())
        self.assertEqual(list(self.root.rglob(".pack-upload-*")), [])

    def test_audit_failure_does_not_delete_preexisting_shared_content(self):
        original = self.put()
        self.db.execute(
            "CREATE TRIGGER reject_upload BEFORE INSERT ON audit "
            "WHEN NEW.action='attachment_put' BEGIN "
            "SELECT RAISE(ABORT,'upload audit rejected'); END"
        )
        self.db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "upload audit rejected"):
            self.put()
        self.assertEqual(self.count("attachments"), 1)
        self.assertTrue(self.file().is_file())
        self.assertEqual(self.file().read_bytes(), PNG)

    def test_tampered_existing_hash_must_not_be_overwritten(self):
        path = self.file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"corrupted")
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.put()
        self.assertEqual(path.read_bytes(), b"corrupted")
        self.assertEqual(self.count("attachments"), 0)

    def test_symlink_target_is_refused_without_overwriting(self):
        path = self.file()
        path.parent.mkdir(parents=True, exist_ok=True)
        secret = self.root / "secret.txt"
        secret.write_text("private", encoding="utf-8")
        path.symlink_to(secret)
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.put()
        self.assertEqual(secret.read_text(encoding="utf-8"), "private")
        self.assertEqual(self.count("attachments"), 0)

    def test_independent_contexts_cannot_both_exceed_room_quota(self):
        other = HubContext(
            base_dir=self.context.base_dir,
            db_path=self.context.db_path,
            attachment_dir=self.context.attachment_dir,
            lock=threading.Lock(), clock=lambda: 12000.0,
        )
        barrier = threading.Barrier(2)
        outcomes = []
        def upload(context):
            barrier.wait(timeout=5)
            try:
                result = self.put(
                    context=context, room_quota=2*len(PNG)-1,
                )
                outcomes.append(("ok", result["attachment_id"]))
            except Exception as error:
                outcomes.append(("error", str(error)))
        threads = [
            threading.Thread(target=upload, args=(self.context,)),
            threading.Thread(target=upload, args=(other,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual([x[0] for x in outcomes].count("ok"), 1)
        self.assertEqual([x[0] for x in outcomes].count("error"), 1)
        self.assertIn("room attachment quota", next(
            x[1] for x in outcomes if x[0]=="error"
        ))
        self.assertEqual(self.count("attachments"), 1)
        self.assertEqual(self.file().read_bytes(), PNG)
        self.assertEqual(list(self.root.rglob(".pack-upload-*")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
