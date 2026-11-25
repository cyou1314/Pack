#!/usr/bin/env python3
"""Regression tests for the additive Agent Registry schema migration."""
import sqlite3
import re
import tempfile
import threading
import unittest
from pathlib import Path

from core import migrations
from core.migrations import apply_migrations
from core.storage import connect
from services.agent_events import append_agent_event


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
            CREATE TABLE audit(
                id INTEGER PRIMARY KEY, ts REAL NOT NULL, actor TEXT NOT NULL,
                action TEXT NOT NULL, detail TEXT
            );
            CREATE TABLE messages(
                id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL,
                author_id INTEGER NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL,
                task_id INTEGER, created_at REAL NOT NULL,
                deleted_at REAL, edited_at REAL
            );
            CREATE TABLE review_log(
                id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE tasks(
                id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL,
                title TEXT NOT NULL, body TEXT NOT NULL,
                created_by INTEGER NOT NULL, assignee INTEGER, status TEXT NOT NULL,
                claim_token TEXT, lease_expires REAL,
                attempts INTEGER NOT NULL DEFAULT 0, result TEXT, envelope TEXT,
                review_note TEXT, fail_class TEXT, next_claim_at REAL,
                sched_at REAL, urgent INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE task_transfers(
                id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL,
                from_member TEXT NOT NULL, to_member TEXT NOT NULL, reason TEXT,
                actor TEXT NOT NULL, created_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', accepted_at REAL, expires_at REAL
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
                        "SELECT version,name FROM hub_schema_migrations WHERE version=1"
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
                    7,
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO agents(agent_id,provider_id,display_name,status,created_at,updated_at) "
                        "VALUES('invalid','missing-provider','Invalid','pending',1,1)"
                    )
                connection.rollback()
            finally:
                connection.close()

    def test_structured_task_audit_backfill_is_exact_bounded_and_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="pack-task-audit-migration-") as directory:
            connection = self._database(directory)
            try:
                details = (
                    "task 1",
                    "task 1: assigned",
                    "task 1 completed",
                    "task 12 ignored",
                    "task 01 leading zero",
                    "task 1x suffix",
                    "task 9223372036854775808 overflow",
                    "task 9223372036854775807 boundary",
                )
                connection.executemany(
                    "INSERT INTO audit(ts,actor,action,detail) VALUES(1,'legacy','test',?)",
                    [(detail,) for detail in details],
                )
                connection.executemany(
                    "INSERT INTO messages(room_id,author_id,kind,body,task_id,created_at) "
                    "VALUES(1,1,'approval_request',?,NULL,1)",
                    [("申请批准 任务#1：旧请求",), ("申请批准 任务#9223372036854775808：越界",)],
                )
                connection.commit()

                apply_migrations(connection, clock=lambda: 100.0)
                rows = connection.execute(
                    "SELECT detail,task_id FROM audit ORDER BY id"
                ).fetchall()
                self.assertEqual(
                    [(row["detail"], row["task_id"]) for row in rows],
                    [
                        ("task 1", 1),
                        ("task 1: assigned", 1),
                        ("task 1 completed", 1),
                        ("task 12 ignored", 12),
                        ("task 01 leading zero", None),
                        ("task 1x suffix", None),
                        ("task 9223372036854775808 overflow", None),
                        ("task 9223372036854775807 boundary", 9223372036854775807),
                    ],
                )
                indexes = {
                    row["name"] for row in connection.execute("PRAGMA index_list(audit)")
                }
                self.assertIn("idx_audit_task", indexes)
                approval_ids = [
                    row["task_id"] for row in connection.execute(
                        "SELECT task_id FROM messages ORDER BY id"
                    )
                ]
                self.assertEqual(approval_ids, [1, None])

                apply_migrations(connection, clock=lambda: 200.0)
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM hub_schema_migrations").fetchone()[0],
                    7,
                )
                self.assertEqual(
                    [row["task_id"] for row in connection.execute(
                        "SELECT task_id FROM audit ORDER BY id"
                    )],
                    [1, 1, 1, 12, None, None, None, 9223372036854775807],
                )
            finally:
                connection.close()

    def test_task_audit_backfill_changes_are_detected_by_ledger_checksum(self):
        from core import migrations

        with tempfile.TemporaryDirectory(prefix="pack-task-audit-checksum-") as directory:
            connection = self._database(directory)
            original = migrations.APPROVAL_TASK_RE
            try:
                apply_migrations(connection, clock=lambda: 100.0)
                migrations.APPROVAL_TASK_RE = re.compile(r"task:([0-9]+)")
                with self.assertRaisesRegex(RuntimeError, "checksum mismatch at version 2"):
                    apply_migrations(connection, clock=lambda: 200.0)
            finally:
                migrations.APPROVAL_TASK_RE = original
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
                    7,
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
                    INSERT INTO messages(id,room_id,author_id,kind,body,task_id,created_at) VALUES(
                        41,1,1,'chat','legacy-message-sentinel',NULL,11
                    );
                    INSERT INTO tasks(
                        id,room_id,title,body,created_by,assignee,status,created_at,updated_at
                    ) VALUES(
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

    def test_v7_adds_projection_tables_and_preserves_existing_session_rows(self):
        with tempfile.TemporaryDirectory(prefix="pack-agent-session-projection-v7-") as directory:
            connection = self._database(directory)
            original = migrations.MIGRATIONS
            try:
                migrations.MIGRATIONS = original[:-1]
                apply_migrations(connection, clock=lambda: 100.0)
                connection.execute(
                    """INSERT INTO agents(
                        agent_id,provider_id,display_name,status,created_at,updated_at
                    ) VALUES('agent-1','codex','Agent One','active',1,1)"""
                )
                connection.execute(
                    """INSERT INTO agent_sessions(
                        session_id,agent_id,room_id,provider_session_id,status,
                        last_event_sequence,created_at,updated_at
                    ) VALUES('hub-session-1','agent-1',1,'provider-thread-1','active',3,1,1)"""
                )
                connection.execute(
                    """INSERT INTO agent_sessions(
                        session_id,agent_id,room_id,provider_session_id,status,
                        last_event_sequence,created_at,updated_at
                    ) VALUES('hub-session-null-1','agent-1',1,NULL,'creating',0,1,1)"""
                )
                connection.commit()

                migrations.MIGRATIONS = original
                apply_migrations(connection, clock=lambda: 200.0)

                version7 = connection.execute(
                    "SELECT version,name FROM hub_schema_migrations WHERE version=7"
                ).fetchone()
                self.assertEqual(
                    (version7["version"], version7["name"]),
                    (7, "agent_session_projection_read_models"),
                )
                session = connection.execute(
                    "SELECT session_id,provider_session_id,last_inbox_seq "
                    "FROM agent_sessions WHERE session_id='hub-session-1'"
                ).fetchone()
                self.assertEqual(
                    tuple(session),
                    ("hub-session-1", "provider-thread-1", 0),
                )
                tables = {
                    row["name"] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertTrue({
                    "agent_session_messages",
                    "agent_session_runs",
                    "agent_event_projection_receipts",
                    "agent_event_projection_resolutions",
                }.issubset(tables))
                indexes = {
                    row["name"] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='index'"
                    )
                }
                self.assertIn("idx_agent_sessions_agent_provider_session_unique", indexes)
                self.assertIn("idx_agent_event_inbox_seq_agent_unique", indexes)
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

                connection.execute(
                    """INSERT INTO agents(
                        agent_id,provider_id,display_name,status,created_at,updated_at
                    ) VALUES('agent-2','codex','Agent Two','active',1,1)"""
                )
                connection.execute(
                    """INSERT INTO agent_sessions(
                        session_id,agent_id,room_id,provider_session_id,status,
                        last_event_sequence,created_at,updated_at
                    ) VALUES('hub-session-2','agent-2',1,'provider-thread-1','active',0,1,1)"""
                )
                connection.execute(
                    """INSERT INTO agent_sessions(
                        session_id,agent_id,room_id,provider_session_id,status,
                        last_event_sequence,created_at,updated_at
                    ) VALUES('hub-session-null-2','agent-1',1,NULL,'creating',0,1,1)"""
                )
                connection.commit()
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """INSERT INTO agent_sessions(
                            session_id,agent_id,room_id,provider_session_id,status,
                            last_event_sequence,created_at,updated_at
                        ) VALUES('hub-session-duplicate','agent-1',1,
                            'provider-thread-1','active',0,1,1)"""
                    )
                connection.rollback()

                event = {
                    "eventId": "projection-migration-event",
                    "eventType": "run.progress",
                    "providerId": "codex",
                    "agentId": "agent-1",
                    "sequence": 1,
                    "occurredAt": "2026-11-24T03:00:00.000Z",
                    "payload": {"progress": 25},
                }
                connection.execute("BEGIN IMMEDIATE")
                inbox = append_agent_event(connection, event, received_at=10.0)
                connection.commit()
                inbox_seq = inbox["inboxSeq"]

                connection.execute(
                    """INSERT INTO agent_session_messages(
                        message_id,session_id,provider_message_id,provider_run_id,
                        content,status,phase,first_inbox_seq,last_inbox_seq,
                        created_at,updated_at,completed_at
                    ) VALUES('message-record-1','hub-session-1','item-1',NULL,
                        'partial','streaming',NULL,?,?,1,1,NULL)""",
                    (inbox_seq, inbox_seq),
                )
                connection.execute(
                    """INSERT INTO agent_session_runs(
                        run_record_id,session_id,provider_run_id,status,started_at,
                        updated_at,ended_at,first_inbox_seq,last_inbox_seq
                    ) VALUES('run-record-1','hub-session-1','provider-run-1',
                        'running',1,1,NULL,?,?)""",
                    (inbox_seq, inbox_seq),
                )
                connection.execute(
                    """INSERT INTO agent_event_projection_receipts(
                        consumer_id,agent_id,inbox_seq,event_id,event_type,outcome,
                        reason_code,attempt_count,first_attempt_at,last_attempt_at,projected_at
                    ) VALUES('hub-session-projector','agent-1',?,
                        'projection-migration-event','run.progress','blocked',
                        'test_blocked',1,1,1,NULL)""",
                    (inbox_seq,),
                )
                connection.execute(
                    """INSERT INTO agent_event_projection_resolutions(
                        resolution_id,consumer_id,agent_id,inbox_seq,event_id,
                        action,reason,actor,resolved_at
                    ) VALUES('resolution-1','hub-session-projector','agent-1',?,
                        'projection-migration-event','skip','test resolution',
                        'dispatcher:test',2)""",
                    (inbox_seq,),
                )
                connection.commit()

                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """INSERT INTO agent_event_projection_receipts(
                            consumer_id,agent_id,inbox_seq,event_id,event_type,outcome,
                            reason_code,attempt_count,first_attempt_at,last_attempt_at,projected_at
                        ) VALUES('hub-session-projector','agent-2',?,
                            'cross-agent-event','run.progress','blocked',
                            'test_blocked',1,1,1,NULL)""",
                        (inbox_seq,),
                    )
                connection.rollback()
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        """INSERT INTO agent_session_messages(
                            message_id,session_id,provider_message_id,content,status,
                            first_inbox_seq,last_inbox_seq,created_at,updated_at
                        ) VALUES('message-large','hub-session-1','item-large',?,'streaming',?,?,1,1)""",
                        ("x" * (4 * 1024 * 1024 + 1), inbox_seq, inbox_seq),
                    )
                connection.rollback()
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            finally:
                migrations.MIGRATIONS = original
                connection.close()

    def test_v7_preflight_fails_closed_on_duplicate_provider_session_binding(self):
        with tempfile.TemporaryDirectory(prefix="pack-agent-session-projection-duplicate-") as directory:
            connection = self._database(directory)
            original = migrations.MIGRATIONS
            try:
                migrations.MIGRATIONS = original[:-1]
                apply_migrations(connection, clock=lambda: 100.0)
                connection.execute(
                    """INSERT INTO agents(
                        agent_id,provider_id,display_name,status,created_at,updated_at
                    ) VALUES('agent-duplicate','codex','Duplicate Agent','active',1,1)"""
                )
                connection.executemany(
                    """INSERT INTO agent_sessions(
                        session_id,agent_id,room_id,provider_session_id,status,
                        last_event_sequence,created_at,updated_at
                    ) VALUES(?,?,1,'same-provider-thread','active',0,1,1)""",
                    [
                        ("hub-session-duplicate-1", "agent-duplicate"),
                        ("hub-session-duplicate-2", "agent-duplicate"),
                    ],
                )
                connection.commit()
                migrations.MIGRATIONS = original
                with self.assertRaisesRegex(RuntimeError, "migration v7 blocked"):
                    apply_migrations(connection, clock=lambda: 200.0)

                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM hub_schema_migrations WHERE version=6"
                    ).fetchone()[0],
                    1,
                )
                columns = {
                    row["name"] for row in connection.execute(
                        "PRAGMA table_info(agent_sessions)"
                    )
                }
                self.assertNotIn("last_inbox_seq", columns)
                tables = {
                    row["name"] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertNotIn("agent_session_messages", tables)
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM agent_sessions WHERE agent_id='agent-duplicate'"
                    ).fetchone()[0],
                    2,
                )
            finally:
                migrations.MIGRATIONS = original
                connection.close()

    def test_v7_preflight_fails_closed_on_blank_provider_session_binding(self):
        with tempfile.TemporaryDirectory(prefix="pack-agent-session-projection-blank-") as directory:
            connection = self._database(directory)
            original = migrations.MIGRATIONS
            try:
                migrations.MIGRATIONS = original[:-1]
                apply_migrations(connection, clock=lambda: 100.0)
                connection.execute(
                    """INSERT INTO agents(
                        agent_id,provider_id,display_name,status,created_at,updated_at
                    ) VALUES('agent-blank','codex','Blank Agent','active',1,1)"""
                )
                connection.execute(
                    """INSERT INTO agent_sessions(
                        session_id,agent_id,room_id,provider_session_id,status,
                        last_event_sequence,created_at,updated_at
                    ) VALUES('hub-session-blank','agent-blank',1,'   ','active',0,1,1)"""
                )
                connection.commit()
                migrations.MIGRATIONS = original
                with self.assertRaisesRegex(RuntimeError, "migration v7 blocked"):
                    apply_migrations(connection, clock=lambda: 200.0)
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM hub_schema_migrations WHERE version=6"
                    ).fetchone()[0],
                    1,
                )
                columns = {
                    row["name"] for row in connection.execute(
                        "PRAGMA table_info(agent_sessions)"
                    )
                }
                self.assertNotIn("last_inbox_seq", columns)
            finally:
                migrations.MIGRATIONS = original
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
