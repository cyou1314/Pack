#!/usr/bin/env python3
"""Regression tests for the additive Agent Registry schema migration."""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from core.migrations import apply_migrations
from core.storage import connect


class AgentRegistryMigrationTests(unittest.TestCase):
    def _database(self, directory: str):
        path = Path(directory) / "registry.db"
        connection = connect(path)
        connection.executescript(
            """
            CREATE TABLE members(
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at REAL NOT NULL,
                removed_at REAL,
                provider TEXT,
                display_name TEXT,
                kind TEXT,
                login_state TEXT,
                token_hash TEXT NOT NULL DEFAULT '',
                profile TEXT,
                bound_room_id INTEGER,
                joined_msg_id INTEGER
            );
            CREATE TABLE rooms(
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            INSERT INTO rooms(id,name,created_at) VALUES(1,'room-a',1);
            INSERT INTO members(id,name,role,created_at,provider,display_name,kind,login_state)
                VALUES(1,'codex-user','member',10,'Codex',NULL,NULL,'logged_in');
            INSERT INTO members(id,name,role,created_at,provider,display_name,kind)
                VALUES(2,'external-user','member',20,'codex','External Name','external');
            INSERT INTO members(id,name,role,created_at,removed_at,provider,display_name,kind)
                VALUES(3,'removed-user','member',30,25,'','Removed Name',NULL);
            INSERT INTO members(id,name,role,created_at)
                VALUES(4,'dispatcher','dispatcher',40);
            INSERT INTO members(id,name,role,created_at,provider,display_name,kind)
                VALUES(5,'unknown-provider-user','member',50,'unknown-vendor',NULL,NULL);
            INSERT INTO members(id,name,role,created_at,provider,login_state)
                VALUES(6,'pending-user','member',60,'muse','pending');
            INSERT INTO members(id,name,role,created_at,provider,login_state)
                VALUES(7,'expired-user','member',70,'muse','expired');
            """
        )
        connection.commit()
        return connection

    def test_migration_maps_legacy_members_without_changing_them(self):
        with tempfile.TemporaryDirectory(prefix="pack-agent-registry-") as directory:
            connection = self._database(directory)
            try:
                before = connection.execute(
                    "SELECT id,name,role,created_at,removed_at,provider,display_name,kind,login_state,"
                    "token_hash,profile,bound_room_id,joined_msg_id "
                    "FROM members ORDER BY id"
                ).fetchall()
                apply_migrations(connection, clock=lambda: 100.0)
                after = connection.execute(
                    "SELECT id,name,role,created_at,removed_at,provider,display_name,kind,login_state,"
                    "token_hash,profile,bound_room_id,joined_msg_id "
                    "FROM members ORDER BY id"
                ).fetchall()
                self.assertEqual([tuple(row) for row in after], [tuple(row) for row in before])

                agents = connection.execute(
                    "SELECT agent_id,provider_id,legacy_member_id,display_name,"
                    "connection_mode,status,legacy_provider_label,created_at,updated_at "
                    "FROM agents ORDER BY legacy_member_id"
                ).fetchall()
                self.assertEqual(len(agents), 6)
                provider_ids = {
                    row[0] for row in connection.execute("SELECT provider_id FROM providers")
                }
                self.assertNotIn("external", provider_ids)
                self.assertEqual(
                    tuple(agents[0]),
                    ("legacy-member-1", "codex", 1, "codex-user", None,
                     "pending", "Codex", 10.0, 100.0),
                )
                self.assertEqual(
                    tuple(agents[1]),
                    ("legacy-member-2", "codex", 2, "External Name",
                     "external_agent", "active", "codex", 20.0, 100.0),
                )
                self.assertEqual(agents[2]["status"], "removed")
                self.assertEqual(agents[2]["provider_id"], "legacy")
                self.assertEqual(agents[3]["provider_id"], "legacy")
                self.assertEqual(agents[3]["legacy_provider_label"], "unknown-vendor")
                self.assertEqual(agents[4]["status"], "pending")
                self.assertEqual(agents[5]["status"], "disconnected")
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM agent_connections").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM agent_sessions").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT version,name FROM hub_schema_migrations"
                    ).fetchone()["name"],
                    "additive_agent_registry",
                )
            finally:
                connection.close()

    def test_migration_is_idempotent_and_foreign_keys_are_enforced(self):
        with tempfile.TemporaryDirectory(prefix="pack-agent-registry-") as directory:
            connection = self._database(directory)
            try:
                apply_migrations(connection, clock=lambda: 100.0)
                first = connection.execute(
                    "SELECT agent_id,legacy_member_id FROM agents ORDER BY agent_id"
                ).fetchall()
                apply_migrations(connection, clock=lambda: 200.0)
                second = connection.execute(
                    "SELECT agent_id,legacy_member_id FROM agents ORDER BY agent_id"
                ).fetchall()
                self.assertEqual([tuple(row) for row in first], [tuple(row) for row in second])
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM hub_schema_migrations").fetchone()[0],
                    1,
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO agents(agent_id,provider_id,display_name,status,created_at,updated_at) "
                        "VALUES('invalid','missing-provider','Invalid','pending',1,1)"
                    )
                connection.rollback()
            finally:
                connection.close()

    def test_backfill_mapping_changes_are_detected_by_ledger_checksum(self):
        from core import migrations

        with tempfile.TemporaryDirectory(prefix="pack-agent-registry-") as directory:
            connection = self._database(directory)
            original = dict(migrations._PROVIDER_LABELS)
            try:
                apply_migrations(connection, clock=lambda: 100.0)
                migrations._PROVIDER_LABELS["codex"] = ("codex", "Changed Vendor")
                with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                    apply_migrations(connection, clock=lambda: 200.0)
            finally:
                migrations._PROVIDER_LABELS.clear()
                migrations._PROVIDER_LABELS.update(original)
                connection.close()


    def test_concurrent_migration_initialization_is_serialized(self):
        with tempfile.TemporaryDirectory(prefix="pack-agent-registry-concurrent-") as directory:
            seed = self._database(directory)
            path = Path(directory) / "registry.db"
            seed.close()
            barrier = threading.Barrier(2)
            errors = []

            def migrate():
                connection = None
                try:
                    connection = connect(path)
                    barrier.wait(timeout=5)
                    apply_migrations(connection, clock=lambda: 100.0)
                except Exception as error:
                    errors.append(error)
                finally:
                    if connection is not None:
                        connection.close()

            workers = [threading.Thread(target=migrate) for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=15)

            self.assertFalse(any(worker.is_alive() for worker in workers))
            self.assertEqual(errors, [])
            verify = connect(path)
            try:
                self.assertEqual(
                    verify.execute("SELECT COUNT(*) FROM hub_schema_migrations").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    verify.execute("SELECT COUNT(*) FROM agents").fetchone()[0],
                    6,
                )
            finally:
                verify.close()


    def test_migration_preserves_legacy_references_and_private_memory(self):
        with tempfile.TemporaryDirectory(prefix="pack-agent-registry-history-") as directory:
            connection = self._database(directory)
            try:
                connection.execute(
                    "UPDATE members SET token_hash=?,profile=?,bound_room_id=?,joined_msg_id=? "
                    "WHERE id=1",
                    ("sha256-token-hash-sentinel", "legacy-profile-sentinel", 1, 41),
                )
                connection.executescript(
                    """
                    CREATE TABLE messages(
                        id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL,
                        author_id INTEGER NOT NULL, kind TEXT NOT NULL,
                        body TEXT NOT NULL, created_at REAL NOT NULL
                    );
                    CREATE TABLE tasks(
                        id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL,
                        title TEXT NOT NULL, body TEXT NOT NULL,
                        created_by INTEGER NOT NULL, assignee INTEGER,
                        status TEXT NOT NULL, created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    );
                    CREATE TABLE runs(
                        id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL,
                        member_id INTEGER NOT NULL, fence INTEGER NOT NULL,
                        status TEXT NOT NULL
                    );
                    CREATE TABLE member_memory(
                        member TEXT NOT NULL, scope TEXT NOT NULL, key TEXT NOT NULL,
                        text TEXT NOT NULL, version_id INTEGER NOT NULL,
                        updated_at REAL NOT NULL,
                        PRIMARY KEY(member,scope,key)
                    );
                    CREATE TABLE member_memory_versions(
                        id INTEGER PRIMARY KEY, member TEXT NOT NULL,
                        scope TEXT NOT NULL, key TEXT NOT NULL,
                        text TEXT NOT NULL, author TEXT NOT NULL,
                        origin TEXT NOT NULL, source_refs TEXT NOT NULL,
                        size INTEGER NOT NULL, created_at REAL NOT NULL
                    );
                    CREATE TABLE member_skills(
                        member_id INTEGER NOT NULL, skill_id INTEGER NOT NULL,
                        enabled INTEGER NOT NULL
                    );
                    CREATE TABLE member_read_cursors(
                        member_id INTEGER NOT NULL, room_id INTEGER NOT NULL,
                        cursor_id INTEGER NOT NULL
                    );
                    CREATE TABLE message_op_keys(
                        member_id INTEGER NOT NULL, op_key TEXT NOT NULL,
                        message_id INTEGER, created_at REAL NOT NULL
                    );
                    INSERT INTO messages VALUES(
                        41,1,1,'chat','legacy-message-sentinel',11
                    );
                    INSERT INTO tasks VALUES(
                        51,1,'legacy-task-sentinel','task-body-sentinel',
                        1,1,'working',12,13
                    );
                    INSERT INTO runs VALUES(61,51,1,1,'running');
                    INSERT INTO member_memory VALUES(
                        'codex-user','private','notes',
                        'private-memory-sentinel',1,14
                    );
                    INSERT INTO member_memory_versions VALUES(
                        71,'codex-user','private','notes',
                        'private-memory-version-sentinel','codex-user',
                        'manual','[]',29,14
                    );
                    INSERT INTO member_skills VALUES(1,81,1);
                    INSERT INTO member_read_cursors VALUES(1,1,41);
                    INSERT INTO message_op_keys VALUES(1,'operation-sentinel',41,15);
                    """
                )
                connection.commit()

                legacy_tables = (
                    "members", "rooms", "messages", "tasks", "runs",
                    "member_memory", "member_memory_versions", "member_skills",
                    "member_read_cursors", "message_op_keys",
                )

                def snapshot():
                    result = {}
                    for table in legacy_tables:
                        schema = connection.execute(
                            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                            (table,),
                        ).fetchone()[0]
                        rows = [
                            tuple(row)
                            for row in connection.execute(
                                f'SELECT * FROM "{table}" ORDER BY rowid'
                            ).fetchall()
                        ]
                        result[table] = {"schema": schema, "rows": rows}
                    return result

                before = snapshot()
                apply_migrations(connection, clock=lambda: 100.0)
                after = snapshot()

                self.assertEqual(after, before)
                agent_columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(agents)")
                }
                self.assertNotIn("token_hash", agent_columns)
                self.assertNotIn("profile", agent_columns)
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM agent_connections").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM agent_sessions").fetchone()[0],
                    0,
                )
            finally:
                connection.close()

    def test_failed_migration_rolls_back_schema_and_ledger(self):
        with tempfile.TemporaryDirectory(prefix="pack-agent-registry-") as directory:
            path = Path(directory) / "broken.db"
            connection = connect(path)
            try:
                connection.execute(
                    "CREATE TABLE members(id INTEGER PRIMARY KEY,name TEXT,role TEXT)"
                )
                connection.commit()
                with self.assertRaisesRegex(RuntimeError, "requires members columns"):
                    apply_migrations(connection, clock=lambda: 100.0)
                names = {
                    row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertNotIn("providers", names)
                self.assertNotIn("agents", names)
                self.assertNotIn("hub_schema_migrations", names)
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
