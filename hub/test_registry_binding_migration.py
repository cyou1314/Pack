"""Versioned binding extension and activation diagnostics on disposable DBs."""
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from event_gateway_test_context import HubContext
from registry_offline_chain import apply_offline_chain
from collaboration_migrate import migrate, inspect_database, MigrationError
from registry_binding_migration import apply_binding_extension, inspect_activation
from collaboration_connection_binding import SCHEMA
from registry_ddl_compare import normalize_ddl


class BindingMigrationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix="pack-binding-migration-")
        self.addCleanup(tmp.cleanup)
        root=Path(tmp.name)
        self.context=HubContext(str(root),str(root/"hub.db"),str(root/"attachments"),
                                threading.RLock(),lambda:100.0)
        old=(hub.BASE,hub.DB_PATH,hub.ATTACH_DIR)
        try:
            hub.BASE,hub.DB_PATH,hub.ATTACH_DIR=(str(root),str(root/"hub.db"),str(root/"attachments"))
            hub.init()
        finally:
            hub.BASE,hub.DB_PATH,hub.ATTACH_DIR=old
        migrate(self.context.db_path,enabled=True)
        self.db=self.context.connect()
        self.addCleanup(self.db.close)
        apply_offline_chain(self.db,now=100)

    def apply(self):
        return apply_binding_extension(self.db,now=101)

    def test_versioned_install_empty_replay_and_legacy_validation(self):
        before=tuple(self.db.execute("SELECT * FROM pack_schema_migrations").fetchone())
        self.assertTrue(self.apply())
        self.assertFalse(self.apply())
        self.assertEqual(tuple(self.db.execute("SELECT * FROM pack_schema_migrations").fetchone()),before)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM collaboration_connection_bindings").fetchone()[0],0)
        self.assertEqual(inspect_database(self.context.db_path)["status"],"already_enabled")
        self.db.execute("BEGIN")
        report=inspect_activation(self.db)
        self.assertTrue(report["binding_schema_ready"])
        self.assertTrue(report["baseline_shadow_ok"])
        self.assertFalse(report["production_activation"])
        self.assertFalse(report["authorization_ready"])
        self.db.rollback()

    def test_unversioned_binding_is_rejected(self):
        self.db.execute(SCHEMA)
        with self.assertRaises(MigrationError):
            self.apply()

    def test_tampered_ledger_is_rejected(self):
        self.apply()
        self.db.execute("UPDATE pack_registry_binding_migrations SET checksum='bad'")
        self.db.commit()
        with self.assertRaises(MigrationError):
            self.apply()

    def test_shadow_drift_blocks_first_install(self):
        self.db.execute("INSERT INTO members(name,role,token_hash,created_at,provider) "
                        "VALUES('late','member','late-fixture',1,'muse')")
        self.db.commit()
        with self.assertRaisesRegex(RuntimeError,"shadow mismatch"):
            self.apply()
        self.assertIsNone(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='collaboration_connection_bindings'").fetchone())

    def test_missing_binding_schema_does_not_authorize(self):
        self.db.execute("BEGIN")
        report=inspect_activation(self.db)
        self.assertFalse(report["binding_schema_ready"])
        self.assertTrue(report["baseline_shadow_ok"])
        self.assertFalse(report["authorization_ready"])
        self.db.rollback()

    def test_literal_comparison_preserves_case_space_and_quotes(self):
        a="CREATE INDEX IF NOT EXISTS i ON t(x) WHERE status='open'"
        self.assertEqual(normalize_ddl(a),normalize_ddl("create index i on t ( x ) where STATUS = 'open'"))
        for s in ("'OPEN'","'o pen'","open"):
            self.assertNotEqual(normalize_ddl(a),normalize_ddl(a.replace("'open'",s)))


if __name__=="__main__":
    unittest.main()
