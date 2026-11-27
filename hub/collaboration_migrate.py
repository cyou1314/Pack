#!/usr/bin/env python3
"""Explicit, backed-up, additive migration for the optional collaboration ledger."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import uuid

from collaboration_store import SCHEMA
from collaboration_guard import GUARDS

VERSION = 1
NAME = "optional-collaboration-ledger-and-task-guards"
TABLE_COLUMNS = {
    "collaboration_instance": {"singleton", "instance_id"},
    "collaboration_runs": {"id", "room_id", "source_message_id", "plan", "max_calls",
                           "calls_used", "deadline", "status"},
    "collaboration_steps": {"run_id", "step_key", "member", "role", "status", "fence"},
    "collaboration_dependencies": {"run_id", "step_key", "prerequisite"},
    "collaboration_task_links": {"run_id", "step_key", "task_id", "member_id"},
    "collaboration_usage": {"task_id", "execution_id", "outcome", "input_tokens",
                            "output_tokens", "reported_at"},
    "collaboration_requests": {"owner_id", "request_key", "source_message_id", "run_id", "body"},
    "collaboration_publications": {"run_id", "message_id", "result_sha256", "published_at"},
    "collaboration_executors": {"member_id", "owner_id", "provider", "access_kind",
                                "account_ref", "revoked"},
    "collaboration_presence": {"member_id", "session_id", "expires_at", "stopped"},
    "collaboration_context_grants": {"run_id", "step_key", "owner_id", "member_id",
                                     "provider", "manifest", "revoked"},
}
TRIGGERS = {
    "collaboration_task_identity_v1", "collaboration_task_transition_v1",
    "collaboration_task_delete_v1", "collaboration_task_replace_v1",
}
HUB_COLUMNS = {
    "tasks": {"id", "room_id", "assignee", "created_by", "title", "body", "status",
              "attempts", "claim_token", "lease_expires"},
    "rooms": {"id", "name"},
    "members": {"id", "name", "role"},
    "runs": {"task_id", "member_id", "fence", "claim_token", "status"},
    "audit": {"id", "ts", "actor", "action"},
}
MIGRATION_SQL = """
CREATE TABLE pack_schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    applied_at REAL NOT NULL,
    schema_sha256 TEXT NOT NULL
);
"""
SCHEMA_HASH = hashlib.sha256((SCHEMA + "\n" + GUARDS).encode("utf-8")).hexdigest()


class MigrationError(RuntimeError):
    pass


def _regular_owned(path, label):
    path = Path(path)
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise MigrationError(f"{label} is unavailable") from exc
    if not stat.S_ISREG(mode) or path.stat().st_uid != os.getuid():
        raise MigrationError(f"{label} must be a regular user-owned file")
    return path


def _database_path(value):
    path = Path(value).absolute()
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir() or parent.stat().st_uid != os.getuid():
        raise MigrationError("Hub database directory must be a real user-owned directory")
    _regular_owned(path, "Hub database")
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.is_symlink():
            raise MigrationError("SQLite sidecar must not be a symlink")
        if sidecar.exists():
            _regular_owned(sidecar, "SQLite sidecar")
    return path


def _backup_directory(db_parent, value):
    root = Path(db_parent).absolute()
    target = Path(value).absolute()
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise MigrationError("migration backup must stay inside the Hub data root") from exc
    if not relative.parts:
        raise MigrationError("migration backup must use a dedicated subdirectory")
    current = root
    for part in relative.parts:
        if part in ("", ".", ".."):
            raise MigrationError("invalid migration backup path")
        current = current / part
        if current.is_symlink():
            raise MigrationError("migration backup path must not contain symlinks")
        if not current.exists():
            current.mkdir(mode=0o700)
        if not current.is_dir() or current.stat().st_uid != os.getuid():
            raise MigrationError("migration backup directory must be user-owned")
        os.chmod(current, 0o700)
    return target


def _connect_readonly(path):
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)


def _integrity(conn):
    row = conn.execute("PRAGMA integrity_check").fetchone()
    if not row or row[0] != "ok":
        raise MigrationError("SQLite integrity_check did not return ok")


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _validate_hub_schema(conn):
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for table, required in HUB_COLUMNS.items():
        if table not in tables or not required <= _columns(conn, table):
            raise MigrationError(f"incompatible Hub database table: {table}")


def _objects(conn):
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    triggers = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger'")}
    return tables, triggers


def _validate_v1(conn):
    tables, triggers = _objects(conn)
    collab_tables = {name for name in tables if name.startswith("collaboration_")}
    collab_triggers = {name for name in triggers if name.startswith("collaboration_")}
    # Only the independently versioned, verified binding extension may
    # accompany the frozen collaboration v1 schema. Do not adopt stray tables.
    binding_ledger = "pack_registry_binding_migrations" in tables
    if "collaboration_connection_bindings" in collab_tables or binding_ledger:
        from registry_binding_migration import validate_binding_extension
        try:
            if not validate_binding_extension(conn):
                raise RuntimeError("missing binding extension")
        except (RuntimeError, sqlite3.Error) as exc:
            raise MigrationError("incompatible binding extension") from exc
        collab_tables.remove("collaboration_connection_bindings")
    if collab_tables != set(TABLE_COLUMNS) or collab_triggers != TRIGGERS:
        raise MigrationError("collaboration schema objects are incomplete or unexpected")
    for table, required in TABLE_COLUMNS.items():
        if not required <= _columns(conn, table):
            raise MigrationError(f"incompatible collaboration table: {table}")
    if not {"version", "name", "applied_at", "schema_sha256"} <= _columns(conn, "pack_schema_migrations"):
        raise MigrationError("incompatible migration ledger")
    rows = conn.execute(
        "SELECT version,name,schema_sha256 FROM pack_schema_migrations ORDER BY version"
    ).fetchall()
    if [tuple(row) for row in rows] != [(VERSION, NAME, SCHEMA_HASH)]:
        raise MigrationError("migration ledger does not match this release")


def _schema_state(conn):
    tables, triggers = _objects(conn)
    marker = "pack_schema_migrations" in tables
    collab = ({name for name in tables if name.startswith("collaboration_")} |
              {name for name in triggers if name.startswith("collaboration_")})
    if not marker:
        if collab or "pack_registry_binding_migrations" in tables:
            raise MigrationError(
                "unversioned collaboration objects found; refusing automatic adoption")
        return "fresh"
    if not collab:
        raise MigrationError("migration ledger exists without collaboration schema")
    _validate_v1(conn)
    return "already_enabled"


def _execute_script(conn, script):
    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            statement = pending.strip()
            if statement:
                conn.execute(statement)
            pending = ""
    if pending.strip():
        raise MigrationError("incomplete migration SQL statement")


def inspect_database(db_path):
    path = _database_path(db_path)
    try:
        with _connect_readonly(path) as conn:
            _integrity(conn)
            _validate_hub_schema(conn)
            state = _schema_state(conn)
            return {"status": state, "database": str(path)}
    except sqlite3.DatabaseError as exc:
        raise MigrationError("Hub database could not be inspected safely") from exc


def _create_backup(db_path, backup_dir):
    directory = _backup_directory(db_path.parent, backup_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = f"pack-pre-collaboration-{stamp}-{uuid.uuid4().hex}"
    backup_path = directory / f"{stem}.db"
    manifest_path = directory / f"{stem}.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(backup_path, flags, 0o600)
    os.close(fd)
    source = target = None
    try:
        source = _connect_readonly(db_path)
        target = sqlite3.connect(str(backup_path), timeout=30)
        source.backup(target, pages=128, sleep=0.01)
        _integrity(target)
        target.close()
        target = None
        source.close()
        source = None
        os.chmod(backup_path, 0o600)
        sync_fd = os.open(backup_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(sync_fd)
        finally:
            os.close(sync_fd)
        digest = hashlib.file_digest(backup_path.open("rb"), "sha256").hexdigest()
        manifest = {
            "schema_version": 1,
            "database_backup": backup_path.name,
            "sha256": digest,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "purpose": "pre-collaboration-schema-migration",
        }
        manifest_fd = os.open(manifest_path, flags, 0o600)
        with os.fdopen(manifest_fd, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=True, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        return {"path": str(backup_path), "manifest": str(manifest_path), "sha256": digest}
    except BaseException:
        if target is not None:
            target.close()
        if source is not None:
            source.close()
        for item in (backup_path, manifest_path):
            try:
                item.unlink()
            except OSError:
                pass
        raise


def _apply(db_path):
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute("BEGIN IMMEDIATE")
        _validate_hub_schema(conn)
        state = _schema_state(conn)
        if state == "already_enabled":
            conn.rollback()
            return state
        _execute_script(conn, SCHEMA)
        _execute_script(conn, GUARDS)
        _execute_script(conn, MIGRATION_SQL)
        conn.execute(
            "INSERT INTO pack_schema_migrations(version,name,applied_at,schema_sha256) "
            "VALUES(?,?,?,?)",
            (VERSION, NAME, datetime.now(timezone.utc).timestamp(), SCHEMA_HASH))
        _validate_v1(conn)
        _integrity(conn)
        conn.commit()
        return "enabled"
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def migrate(db_path, *, enabled, backup_dir=None, dry_run=False):
    if enabled is not True:
        raise MigrationError("explicit --enable-collaboration confirmation is required")
    path = _database_path(db_path)
    state = inspect_database(path)["status"]
    if state == "already_enabled":
        return {"status": state, "database": str(path), "backup": None}
    if dry_run:
        return {"status": "ready", "database": str(path), "backup": None}
    target = backup_dir or path.parent / "backups" / "migrations"
    backup = _create_backup(path, target)
    try:
        result = _apply(path)
    except BaseException as exc:
        raise MigrationError(
            f"migration transaction rolled back; verified pre-migration backup retained at {backup['path']}"
        ) from exc
    return {"status": result, "database": str(path), "backup": backup}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable-collaboration", action="store_true", required=True,
                        help="explicitly opt in to creating the collaboration ledger and task guards")
    parser.add_argument("--dry-run", action="store_true",
                        help="inspect compatibility without writing a backup or changing the database")
    args = parser.parse_args(argv)
    db_path = Path(__file__).absolute().with_name("pack.db")
    try:
        result = migrate(db_path, enabled=args.enable_collaboration, dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, sqlite3.DatabaseError, MigrationError, ValueError) as exc:
        print(f"Collaboration migration refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
