"""Source safety and WAL-aware Registry rehearsal regression."""
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing

import hub
from registry_rehearsal import inspect_source,rehearse,main
from collaboration_migrate import MigrationError


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RegistryRehearsalTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix="pack-rehearsal-")
        self.addCleanup(tmp.cleanup)
        root=Path(tmp.name)
        self.db=root/"pack.db"
        self.output=root/"rehearsals"
        self.output.mkdir(mode=0o700)
        old=(hub.BASE,hub.DB_PATH,hub.ATTACH_DIR)
        try:
            hub.BASE,hub.DB_PATH,hub.ATTACH_DIR=(str(root),str(self.db),str(root/"attachments"))
            hub.init()
        finally:
            hub.BASE,hub.DB_PATH,hub.ATTACH_DIR=old

    def versions(self,path):
        with closing(sqlite3.connect(str(path))) as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE name='hub_schema_migrations'").fetchone():
                return []
            return [r[0] for r in c.execute("SELECT version FROM hub_schema_migrations ORDER BY version")]

    def test_readonly_inspection_creates_no_output(self):
        before=digest(self.db)
        self.assertEqual(inspect_source(self.db)["pending_versions"],list(range(1,9)))
        self.assertEqual(digest(self.db),before)
        self.assertEqual(list(self.output.iterdir()),[])

    def test_rehearsal_keeps_source_backup_and_work_separate(self):
        before=digest(self.db)
        report=rehearse(self.db,self.output)
        self.assertEqual(report["status"],"rehearsed")
        root=Path(report["directory"])
        backup=root/report["backup"]
        work=root/report["rehearsal"]
        self.assertEqual(digest(self.db),before)
        self.assertEqual(digest(backup),report["backup_sha256"])
        self.assertEqual(digest(work),report["rehearsal_sha256"])
        self.assertEqual(self.versions(backup),[])
        self.assertEqual(self.versions(work),list(range(1,9)))
        self.assertEqual(self.versions(self.db),[])
        self.assertFalse(json.loads((root/"manifest.json").read_text())["production_activation"])
        for path in (backup,work,root/"manifest.json",root):
            self.assertEqual(path.stat().st_mode & 0o077,0)
        self.assertNotEqual(rehearse(self.db,self.output)["directory"],report["directory"])

    def test_committed_wal_rows_survive_snapshot(self):
        with closing(sqlite3.connect(str(self.db))) as writer:
            self.assertEqual(writer.execute("PRAGMA journal_mode=WAL").fetchone()[0],"wal")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("INSERT INTO members(name,role,token_hash,created_at,provider,login_state) "
                           "VALUES('wal-fixture','member','wal-fixture',1,'gemini-api','logged_in')")
            writer.commit()
            self.assertTrue(Path(str(self.db)+"-wal").exists())
            report=rehearse(self.db,self.output)
            self.assertEqual(report["status"],"rehearsed")
            root=Path(report["directory"])
            with closing(sqlite3.connect(str(root/report["backup"]))) as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM members WHERE name='wal-fixture'").fetchone()[0],1)
            with closing(sqlite3.connect(str(root/report["rehearsal"]))) as c:
                self.assertEqual(c.execute("SELECT provider_id FROM agents WHERE display_name='wal-fixture'").fetchone()[0],"gemini")

    def test_blocked_migration_retains_verified_backup(self):
        with closing(sqlite3.connect(str(self.db))) as c:
            c.execute("INSERT INTO members(name,role,token_hash,created_at,provider,kind) "
                      "VALUES('conflict','member','conflict',1,'gemini-api','external')")
            c.commit()
        before=digest(self.db)
        report=rehearse(self.db,self.output)
        self.assertEqual(report["status"],"blocked")
        root=Path(report["directory"])
        self.assertEqual(digest(root/report["backup"]),report["backup_sha256"])
        self.assertEqual(digest(self.db),before)
        self.assertIsNone(report["rehearsal"])
        self.assertEqual(self.versions(root/"migration.rehearsal.db"),[])

    def test_symlink_and_public_output_rejected(self):
        link=self.db.with_name("alias.db")
        link.symlink_to(self.db)
        with self.assertRaises(MigrationError):
            inspect_source(link)
        self.output.chmod(0o755)
        with self.assertRaises(MigrationError):
            rehearse(self.db,self.output)

    def test_bad_ledger_fails_readonly_preflight(self):
        with closing(sqlite3.connect(str(self.db))) as c:
            c.execute("CREATE TABLE hub_schema_migrations(version INTEGER,name TEXT,checksum TEXT)")
            c.execute("INSERT INTO hub_schema_migrations VALUES(99,'unknown','bad')")
            c.commit()
        with self.assertRaisesRegex(RuntimeError,"mismatched"):
            inspect_source(self.db)
        self.assertEqual(main(["inspect","--database",str(self.db)]),1)


if __name__=="__main__":
    unittest.main()
