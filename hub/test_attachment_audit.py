#!/usr/bin/env python3
"""Isolated attachment store audit and backup/restore consistency checks."""
import hashlib
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import unittest

import hub
from core.context import HubContext
from services.attachment_audit import audit_attachments, main

PNG = bytes.fromhex("89504e470d0a1a0a") + b"pack-audit-test"


class AttachmentAuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pack-attachment-audit-")
        self.root = Path(self.temporary.name)
        (self.root / "attachments").mkdir()
        self.context = HubContext(
            base_dir=str(self.root), db_path=str(self.root / "pack.db"),
            attachment_dir=str(self.root / "attachments"),
            lock=threading.Lock(), clock=lambda: 12000.0,
        )
        hub.init(self.context)
        self.db = self.context.connect()
        self.dispatcher = self.db.execute(
            "SELECT id FROM members WHERE role='dispatcher'"
        ).fetchone()[0]

    def tearDown(self):
        self.db.close()
        self.temporary.cleanup()

    def make_blob(self, data=PNG, referenced=True, *, created_at=12000.0):
        digest = hashlib.sha256(data).hexdigest()
        path = Path(hub._attach_path(digest, "image/png", self.context))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if referenced:
            self.db.execute(
                "INSERT INTO attachments(sha256,mime,size,original_name,"
                "uploader_id,room_id,created_at)"
                " VALUES(?,'image/png',?,'test.png',?,1,?)",
                (digest, len(data), self.dispatcher, created_at),
            )
            self.db.commit()
        return path

    def report(self):
        return audit_attachments(
            self.root / "pack.db", self.root / "attachments",
            now=200000.0, grace_seconds=86400,
        )

    def test_clean_referenced_blob_is_valid(self):
        self.make_blob()
        report = self.report()
        self.assertTrue(report["ok"])
        self.assertEqual(report["counts"]["db_rows"], 1)
        self.assertEqual(report["counts"]["referenced_blobs"], 1)
        self.assertEqual(report["counts"]["missing"], 0)

    def test_duplicate_metadata_ref_does_not_look_like_orphan(self):
        self.make_blob()
        self.db.execute(
            "INSERT INTO attachments(sha256,mime,size,original_name,"
            "uploader_id,room_id,created_at)"
            " SELECT sha256,mime,size,original_name,uploader_id,room_id,created_at"
            " FROM attachments LIMIT 1"
        )
        self.db.commit()
        report = self.report()
        self.assertTrue(report["ok"])
        self.assertEqual(report["counts"]["db_rows"], 2)
        self.assertEqual(report["counts"]["referenced_blobs"], 1)

    def test_new_orphan_requires_grace_and_old_candidate_is_not_deleted(self):
        path = self.make_blob(referenced=False)
        os.utime(path, (200000, 200000))
        fresh = self.report()
        self.assertTrue(fresh["ok"])
        self.assertEqual(fresh["counts"]["recent_unreferenced"], 1)
        os.utime(path, (5000, 5000))
        old = self.report()
        self.assertFalse(old["ok"])
        self.assertEqual(old["counts"]["stale_orphan_candidates"], 1)
        self.assertEqual(path.read_bytes(), PNG)

    def test_unreferenced_staging_temp_is_reported_only(self):
        path = self.make_blob(referenced=False)
        temporary = path.parent / ".pack-upload-failed"
        temporary.write_bytes(b"tmp")
        os.utime(temporary, (1000, 1000))
        report = self.report()
        self.assertEqual(report["counts"]["stale_temp_candidates"], 1)
        self.assertTrue(temporary.exists())

    def test_db_reference_to_missing_file_is_reported(self):
        path = self.make_blob()
        path.unlink()
        report = self.report()
        self.assertFalse(report["ok"])
        self.assertEqual(report["counts"]["missing"], 1)

    def test_tampered_blob_is_reported_without_modification(self):
        path = self.make_blob()
        data = b"X" * len(PNG)
        path.write_bytes(data)
        report = self.report()
        self.assertEqual(report["counts"]["hash_mismatch"], 1)
        self.assertEqual(path.read_bytes(), data)

    def test_symlink_does_not_get_followed(self):
        path = self.make_blob()
        outside = self.root / "private.txt"
        outside.write_text("secret", encoding="utf-8")
        path.unlink()
        path.symlink_to(outside)
        report = self.report()
        self.assertGreater(report["counts"]["unsafe_path"], 0)
        self.assertEqual(outside.read_text(encoding="utf-8"), "secret")

    def test_invalid_record_reports_without_accessing_fake_sha_path(self):
        self.db.execute(
            "INSERT INTO attachments(sha256,mime,size,original_name,"
            "uploader_id,room_id,created_at)"
            " VALUES('../escape','image/png',10,'bad',?,1,12000)",
            (self.dispatcher,),
        )
        self.db.commit()
        report = self.report()
        self.assertEqual(report["counts"]["invalid_records"], 1)

    def test_cli_strict_reports_failure_without_deleting(self):
        path = self.make_blob(referenced=False)
        os.utime(path, (1000, 1000))
        with redirect_stdout(io.StringIO()) as sink:
            status = main([
                "--db", str(self.root / "pack.db"),
                "--attachments", str(self.root / "attachments"),
                "--grace-hours", "0", "--strict",
            ])
        report = json.loads(sink.getvalue())
        self.assertEqual(status, 1)
        self.assertEqual(report["counts"]["stale_orphan_candidates"], 1)
        self.assertTrue(path.exists())

    def test_backup_restores_referenced_image_and_passes_readonly_audit(self):
        original = self.make_blob()
        backups = self.root / "backups"
        backups.mkdir()
        script_dir = Path(__file__).resolve().parent
        env = {
            **os.environ,
            "PACK_HUB_DIR": str(self.root),
            "PACK_BACKUP_DIR": str(backups),
        }
        for script in ("backup-db.sh", "verify-backup.sh"):
            result = subprocess.run(
                ["bash", str(script_dir / script)], env=env,
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        snapshot = next(backups.glob("pack-*.db"))
        archive = next(backups.glob("attachments-*.tar.gz"))
        restore = self.root / "restore"
        restore.mkdir()
        shutil.copy2(snapshot, restore / "pack.db")
        result = subprocess.run(
            ["tar", "-xzf", str(archive), "-C", str(restore)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        cloned = restore / "attachments" / original.parent.name / original.name
        self.assertEqual(cloned.read_bytes(), PNG)
        audit = audit_attachments(restore / "pack.db", restore / "attachments")
        self.assertTrue(audit["ok"], audit)
        with sqlite3.connect(f"file:{restore / 'pack.db'}?mode=ro", uri=True) as conn:
            self.assertEqual(conn.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0], "ok")
        cloned.unlink()
        broken = audit_attachments(restore / "pack.db", restore / "attachments")
        self.assertEqual(broken["counts"]["missing"], 1)
        self.assertEqual(original.read_bytes(), PNG)


if __name__ == "__main__":
    unittest.main(verbosity=2)
