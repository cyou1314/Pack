"""Offline port of Foundation migrations v2/v3; no startup integration."""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3

from registry_v1_port import (
    Migration, _REGISTRY_V1, _PROVIDER_LABELS, _provider_for,
    _apply_registry_v1, _normalized_function_source,
)
from registry_task_refs import (
    APPROVAL_TASK_RE, AUDIT_TASK_RE, SQLITE_INT_MAX,
    audit_task_id, bounded_task_id,
)

def _checksum(migration: Migration) -> str:
    payload: dict[str, object] = {
        "version": migration.version,
        "name": migration.name,
        "statements": migration.statements,
    }
    if migration.version == 1:
        # The v1 migration includes a data backfill, not just DDL. Cover the
        # provider mapping and normalized source of every helper that changes
        # backfill behavior. Avoid AST dumps: their representation can differ
        # across supported Python versions and block startup on interpreter upgrades.
        payload["data_migration"] = {
            "provider_labels": _PROVIDER_LABELS,
            "provider_for_source": _normalized_function_source(_provider_for),
            "apply_registry_v1_source": _normalized_function_source(_apply_registry_v1),
        }
    elif migration.version == 2:
        # Freeze the structured audit parser and its backfill implementation.
        payload["data_migration"] = {
            "audit_task_pattern": AUDIT_TASK_RE.pattern,
            "approval_task_pattern": APPROVAL_TASK_RE.pattern,
            "sqlite_int_max": SQLITE_INT_MAX,
            "bounded_task_id_source": _normalized_function_source(bounded_task_id),
            "audit_task_id_source": _normalized_function_source(audit_task_id),
            "apply_task_audit_v2_source": _normalized_function_source(_apply_task_audit_v2),
        }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _apply_task_audit_v2(connection: sqlite3.Connection) -> None:
    """Add structured audit ownership, backfill legacy prefixes and index lookups."""
    required_tables = {"audit", "messages", "review_log"}
    existing_tables = {
        row["name"] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing = sorted(required_tables - existing_tables)
    if missing:
        raise RuntimeError(
            "structured task audit migration requires tables: " + ", ".join(missing)
        )

    audit_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(audit)")
    }
    if "task_id" not in audit_columns:
        connection.execute("ALTER TABLE audit ADD COLUMN task_id INTEGER")

    # Preserve existing non-NULL associations and infer only canonical legacy
    # prefixes. Task-like text elsewhere is deliberately left unassociated.
    for row in connection.execute(
        "SELECT id,detail FROM audit WHERE task_id IS NULL ORDER BY id"
    ):
        task_id = audit_task_id(row["detail"])
        if task_id is not None:
            connection.execute(
                "UPDATE audit SET task_id=? WHERE id=? AND task_id IS NULL",
                (task_id, row["id"]),
            )

    # Backfill historical approval requests so normal reads use the partial index.
    for row in connection.execute(
        "SELECT id,body FROM messages "
        "WHERE kind='approval_request' AND task_id IS NULL ORDER BY id"
    ):
        task_id = bounded_task_id(APPROVAL_TASK_RE.search(row["body"] or ""))
        if task_id is not None:
            connection.execute(
                "UPDATE messages SET task_id=? WHERE id=? AND task_id IS NULL",
                (task_id, row["id"]),
            )

    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_audit_task ON audit(task_id,id)",
        """CREATE INDEX IF NOT EXISTS idx_messages_approval_task
           ON messages(task_id,id DESC) WHERE kind='approval_request'""",
        """CREATE INDEX IF NOT EXISTS idx_messages_dispatch_decisions
           ON messages(author_id,id) WHERE kind IN ('chat','decision')""",
        "CREATE INDEX IF NOT EXISTS idx_review_log_task_time "
        "ON review_log(task_id,created_at)",
    ):
        connection.execute(statement)


_TASK_AUDIT_V2 = Migration(
    version=2,
    name="structured_task_audit_and_approval_indexes",
    statements=(),
)


_TASK_HOTPATH_V3 = Migration(
    version=3,
    name="task_queue_lease_and_transfer_hotpath_indexes",
    statements=(
        "CREATE INDEX IF NOT EXISTS idx_tasks_urgent_id "
        "ON tasks(urgent DESC, id ASC)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_assignee_urgent_id "
        "ON tasks(assignee, urgent DESC, id ASC)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_room_urgent_id "
        "ON tasks(room_id, urgent DESC, id ASC)",
        """CREATE INDEX IF NOT EXISTS idx_tasks_active_queue
           ON tasks(urgent DESC, id ASC, assignee)
           WHERE status IN ('open','claimed','working')""",
        """CREATE INDEX IF NOT EXISTS idx_tasks_lease_expiry
           ON tasks(lease_expires)
           WHERE status IN ('claimed','working') AND lease_expires IS NOT NULL""",
        """CREATE INDEX IF NOT EXISTS idx_tasks_unclaimed_due
           ON tasks(assignee, next_claim_at)
           WHERE status='open' AND assignee IS NOT NULL""",
        """CREATE INDEX IF NOT EXISTS idx_task_transfers_pending_expiry
           ON task_transfers(expires_at, id)
           WHERE status='pending' AND expires_at IS NOT NULL""",
    ),
)




# v2 creates these indexes inside its checksum-covered backfill.
_V2_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_audit_task ON audit(task_id,id)",
    "CREATE INDEX IF NOT EXISTS idx_messages_approval_task ON messages(task_id,id DESC) WHERE kind='approval_request'",
    "CREATE INDEX IF NOT EXISTS idx_messages_dispatch_decisions ON messages(author_id,id) WHERE kind IN ('chat','decision')",
    "CREATE INDEX IF NOT EXISTS idx_review_log_task_time ON review_log(task_id,created_at)",
)


from registry_ddl_compare import normalize_ddl as _normalize_index


def _check_indexes(conn, statements, *, required):
    for sql in statements:
        name = re.match(r"CREATE INDEX IF NOT EXISTS (\w+)", sql)[1]
        row = conn.execute(
            "SELECT type,sql FROM sqlite_master WHERE name=?", (name,)
        ).fetchone()
        if row is None:
            if required:
                raise RuntimeError("missing migration index: " + name)
            continue
        if row[0] != "index" or not row[1] or _normalize_index(row[1]) != _normalize_index(sql):
            raise RuntimeError("incompatible migration index: " + name)


def apply_registry_v2_v3(conn, *, now):
    """Apply exact v2 and v3 definitions after v1 on a disposable DB copy.

    Existing non-null task associations remain untouched. Reject mismatched
    ledgers and same-name indexes instead of silently blessing a collision.
    Return the list of newly applied versions; caller owns the database copy.
    """
    if conn.in_transaction:
        raise RuntimeError("migration requires an idle connection")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise RuntimeError("migration requires foreign keys")
    if type(now) not in (int, float) or not math.isfinite(now):
        raise ValueError("invalid migration timestamp")
    old_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        migrations = (_REGISTRY_V1, _TASK_AUDIT_V2, _TASK_HOTPATH_V3)
        rows = conn.execute(
            "SELECT version,name,checksum FROM hub_schema_migrations ORDER BY version"
        ).fetchall()
        expected = [(m.version, m.name, _checksum(m)) for m in migrations]
        if not rows or len(rows) > 3 or [tuple(row) for row in rows] != expected[:len(rows)]:
            raise RuntimeError("unsupported or mismatched Hub migration ledger")
        if [row[0] for row in conn.execute("PRAGMA quick_check")] != ["ok"]:
            raise RuntimeError("database integrity check failed")
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("database foreign key violation")
        _check_indexes(conn, _V2_INDEXES, required=len(rows) >= 2)
        _check_indexes(conn, _TASK_HOTPATH_V3.statements, required=len(rows) >= 3)
        applied = []
        for m in migrations[len(rows):]:
            if m.version == 2:
                _apply_task_audit_v2(conn)
            else:
                for sql in m.statements:
                    conn.execute(sql)
            conn.execute(
                "INSERT INTO hub_schema_migrations(version,name,checksum,applied_at) VALUES(?,?,?,?)",
                (m.version,m.name,_checksum(m),float(now)))
            applied.append(m.version)
        _check_indexes(conn, _V2_INDEXES, required=True)
        _check_indexes(conn, _TASK_HOTPATH_V3.statements, required=True)
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("database foreign key violation")
        conn.commit()
        return applied
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.row_factory = old_factory
