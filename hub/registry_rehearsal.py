#!/usr/bin/env python3
"""Read-only Registry preflight and disposable snapshot rehearsal.

This command has no production apply/restore/activation operation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
from contextlib import closing

from collaboration_migrate import _database_path, _validate_hub_schema, MigrationError
from registry_offline_chain import CHAIN, checksum, _verify_schema, apply_offline_chain


def _readonly(path):
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _inspect(conn):
    _validate_hub_schema(conn)
    if [r[0] for r in conn.execute("PRAGMA integrity_check")] != ["ok"]:
        raise RuntimeError("database integrity check failed")
    if conn.execute("PRAGMA foreign_key_check").fetchone():
        raise RuntimeError("database foreign key violation")
    ledger = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='hub_schema_migrations'"
    ).fetchone()
    rows = conn.execute(
        "SELECT version,name,checksum FROM hub_schema_migrations ORDER BY version"
    ).fetchall() if ledger else []
    expected = [(m.version,m.name,checksum(m)) for m in CHAIN]
    if len(rows)>len(CHAIN) or [tuple(r) for r in rows]!=expected[:len(rows)]:
        raise RuntimeError("unsupported or mismatched Hub migration ledger")
    _verify_schema(conn,len(rows))
    return {"current_version":len(rows),"target_version":len(CHAIN),
            "pending_versions":[m.version for m in CHAIN[len(rows):]]}


def inspect_source(db_path):
    """Read-only structural preflight, not a guarantee of data migration."""
    path = _database_path(db_path)
    with closing(_readonly(path)) as conn:
        conn.execute("BEGIN")
        return _inspect(conn)


def _private_root(value):
    root = Path(value).absolute()
    info = root.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077):
        raise MigrationError("output root must be a private user-owned directory")
    return root


def _new_file(path):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os,"O_NOFOLLOW",0)
    return os.fdopen(os.open(path,flags,0o600),"wb")


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream,"sha256").hexdigest()


def _sync(path):
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def _sync_dir(path):
    fd=os.open(path,os.O_RDONLY | getattr(os,"O_DIRECTORY",0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _snapshot(source, target):
    # SQLite backup includes committed WAL pages and produces a consistent
    # database image, unlike copying the main file of a WAL database.
    with _new_file(target):
        pass
    deadline=time.monotonic()+30
    def progress(status,remaining,total):
        if time.monotonic()>deadline:
            raise TimeoutError("snapshot deadline exceeded")
    with closing(_readonly(source)) as src, closing(sqlite3.connect(str(target),timeout=5)) as dst:
        src.execute("BEGIN")
        state=_inspect(src)
        src.backup(dst,pages=128,progress=progress,sleep=0.01)
        if dst.execute("PRAGMA journal_mode=DELETE").fetchone()[0]!="delete":
            raise RuntimeError("snapshot journal conversion failed")
        if _inspect(dst)!=state:
            raise RuntimeError("snapshot preflight mismatch")
    _sync(target)
    return state


def _manifest(path, result):
    with _new_file(path) as stream:
        stream.write((json.dumps(result,sort_keys=True,ensure_ascii=True)+"\n").encode())
        stream.flush()
        os.fsync(stream.fileno())
    _sync_dir(path.parent)


def rehearse(db_path, output_root):
    """Create an immutable-by-convention snapshot and separate rehearsal copy.

    Never replace, rename, or write to the source DB. On migration failure,
    retain the verified snapshot and report failure without a success marker.
    """
    source=_database_path(db_path)
    root=_private_root(output_root)
    run=Path(tempfile.mkdtemp(prefix="pack-registry-rehearsal-",dir=root))
    backup=run/"source.snapshot.db"
    work=run/"migration.rehearsal.db"
    manifest=run/"manifest.json"
    state=_snapshot(source,backup)
    digest=_digest(backup)
    _sync_dir(run)
    _sync_dir(root)
    result={"status":"blocked","source_version":state["current_version"],
            "target_version":len(CHAIN),"backup":backup.name,
            "backup_sha256":digest,"rehearsal":None,"applied_versions":[],
            "production_activation":False}
    try:
        with backup.open("rb") as src, _new_file(work) as dst:
            shutil.copyfileobj(src,dst)
            dst.flush()
            os.fsync(dst.fileno())
        if _digest(work)!=digest:
            raise RuntimeError("rehearsal copy verification failed")
        with closing(sqlite3.connect(str(work),timeout=5)) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            applied=apply_offline_chain(conn,now=time.time())
            if _inspect(conn)["current_version"]!=len(CHAIN):
                raise RuntimeError("rehearsal ledger incomplete")
            if conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]!="delete":
                raise RuntimeError("rehearsal journal conversion failed")
        _sync(work)
        if _digest(backup)!=digest:
            raise RuntimeError("backup digest changed")
        result.update(status="rehearsed",rehearsal=work.name,
                      rehearsal_sha256=_digest(work),applied_versions=applied)
    except Exception:
        # Do not emit arbitrary SQLite errors or row content in the manifest.
        # The failed copy is retained for controlled local diagnosis.
        result["reason"]="migration_or_verification_failed"
    _manifest(manifest,result)
    return {**result,"directory":str(run)}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest="command",required=True)
    inspect=commands.add_parser("inspect")
    inspect.add_argument("--database",required=True)
    rehearsal=commands.add_parser("rehearse")
    rehearsal.add_argument("--database",required=True)
    rehearsal.add_argument("--output-root",required=True)
    args=parser.parse_args(argv)
    try:
        result=(inspect_source(args.database) if args.command=="inspect"
                else rehearse(args.database,args.output_root))
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 1 if result.get("status")=="blocked" else 0
    except (OSError,RuntimeError,ValueError,sqlite3.Error,MigrationError):
        print("Registry preflight/rehearsal refused; source was not migrated.",file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
