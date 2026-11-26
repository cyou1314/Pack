"""Exercise v2/v3 port on the actual main-based Hub schema."""
import os
import sqlite3
import tempfile
import unittest

from test_task_lifecycle_guards import hub
from registry_v1_port import apply_registry_v1, _REGISTRY_V1, _checksum as v1_checksum
from registry_v2_v3_port import apply_registry_v2_v3, _checksum


class RegistryV2V3Tests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="pack-registry-v23-")
        self.addCleanup(tmp.cleanup)
        for attr, value in (("BASE", tmp.name), ("DB_PATH", os.path.join(tmp.name, "pack.db")),
                            ("ATTACH_DIR", os.path.join(tmp.name, "attachments"))):
            self.addCleanup(setattr, hub, attr, getattr(hub, attr))
            setattr(hub, attr, value)
        hub.init()
        self.conn = hub.db()
        self.addCleanup(self.conn.close)
        self.conn.execute("PRAGMA foreign_keys=ON")
        apply_registry_v1(self.conn, now=10)

    def apply(self):
        return apply_registry_v2_v3(self.conn, now=20)

    def versions(self):
        return [row[0] for row in self.conn.execute(
            "SELECT version FROM hub_schema_migrations ORDER BY version")]

    def test_v1_checksum_and_replay(self):
        self.assertEqual(v1_checksum(_REGISTRY_V1), _checksum(_REGISTRY_V1))
        self.assertEqual(self.apply(), [2,3])
        self.assertEqual(self.versions(), [1,2,3])
        self.assertEqual(self.apply(), [])

    def test_backfill_preserves_existing_associations(self):
        self.conn.executemany(
            "INSERT INTO audit(ts,actor,action,detail,task_id) VALUES(1,'fixture','fixture',?,?)",
            [("task 12: canonical",None),("task 0012: legacy",None),
             ("task 0012: previously associated",12),("task 19",99),
             ("task 9223372036854775808",None)])
        self.conn.commit()
        self.apply()
        rows = self.conn.execute(
            "SELECT task_id FROM audit WHERE actor='fixture' ORDER BY id").fetchall()
        self.assertEqual([r[0] for r in rows],[12,None,12,99,None])

    def test_missing_index_is_added(self):
        self.conn.execute("DROP INDEX idx_tasks_lease_expiry")
        self.assertEqual(self.apply(), [2,3])
        self.assertIsNotNone(self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='idx_tasks_lease_expiry'").fetchone())

    def test_wrong_index_rejected_before_backfill(self):
        self.conn.execute("DROP INDEX idx_tasks_lease_expiry")
        self.conn.execute("CREATE INDEX idx_tasks_lease_expiry ON tasks(id)")
        self.conn.execute("INSERT INTO audit(ts,actor,action,detail) VALUES(1,'fixture','fixture','task 12')")
        self.conn.commit()
        with self.assertRaisesRegex(RuntimeError,"incompatible migration index"):
            self.apply()
        self.assertEqual(self.versions(),[1])
        self.assertIsNone(self.conn.execute("SELECT task_id FROM audit WHERE actor='fixture'").fetchone()[0])

    def test_replay_rejects_missing_index(self):
        self.apply()
        self.conn.execute("DROP INDEX idx_tasks_lease_expiry")
        with self.assertRaisesRegex(RuntimeError,"missing migration index"):
            self.apply()

    def test_ledger_mismatch(self):
        self.conn.execute("UPDATE hub_schema_migrations SET checksum='wrong'")
        self.conn.commit()
        with self.assertRaisesRegex(RuntimeError,"mismatched"):
            self.apply()
        self.assertEqual(self.versions(),[1])

    def test_atomic_failure(self):
        self.conn.execute("DROP INDEX idx_tasks_lease_expiry")
        self.conn.execute("DROP TABLE task_transfers")
        self.conn.execute("INSERT INTO audit(ts,actor,action,detail) VALUES(1,'fixture','fixture','task 12')")
        self.conn.commit()
        with self.assertRaises(sqlite3.OperationalError):
            self.apply()
        self.assertEqual(self.versions(),[1])
        self.assertIsNone(self.conn.execute("SELECT task_id FROM audit WHERE actor='fixture'").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
