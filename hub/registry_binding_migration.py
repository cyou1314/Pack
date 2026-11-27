"""Versioned optional binding extension; disposable copies only.

Separate ledger from frozen collaboration v1 and Foundation v1-v8.
No automatic binding, login inference, startup or production activation.
"""
import hashlib
import math
import sqlite3

from collaboration_connection_binding import SCHEMA
from registry_event_v4_v5_port import _normalize
from agent_registry_shadow import build_shadow_report

VERSION=1
NAME="explicit_registry_connection_binding"
LEDGER="pack_registry_binding_migrations"
TABLE="collaboration_connection_bindings"
LEDGER_SQL="""CREATE TABLE pack_registry_binding_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at REAL NOT NULL
)"""
CHECKSUM=hashlib.sha256((SCHEMA+"\n"+LEDGER_SQL).encode()).hexdigest()


def _object(conn,name):
    return conn.execute("SELECT type,sql FROM sqlite_master WHERE name=?",(name,)).fetchone()


def _verify_object(row,sql):
    return bool(row and row[0]=="table" and row[1] and _normalize(row[1])==_normalize(sql))


def validate_binding_extension(conn):
    """Read-only extension check, independent of base collaboration validation."""
    table,ledger=_object(conn,TABLE),_object(conn,LEDGER)
    if table is None and ledger is None:
        return False
    if not _verify_object(table,SCHEMA) or not _verify_object(ledger,LEDGER_SQL):
        raise RuntimeError("unversioned or incompatible binding extension")
    rows=conn.execute("SELECT version,name,checksum FROM "+LEDGER).fetchall()
    if [tuple(r) for r in rows]!=[(VERSION,NAME,CHECKSUM)]:
        raise RuntimeError("binding extension ledger mismatch")
    return True


def apply_binding_extension(conn, *, now):
    """Install empty versioned table on a disposable fully migrated copy."""
    from collaboration_migrate import _validate_v1
    if conn.in_transaction:
        raise RuntimeError("binding migration requires idle connection")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0]!=1:
        raise RuntimeError("binding migration requires foreign keys")
    if type(now) not in (int,float) or not math.isfinite(now):
        raise ValueError("invalid timestamp")
    try:
        conn.execute("BEGIN IMMEDIATE")
        _validate_v1(conn)
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("database foreign key violation")
        if validate_binding_extension(conn):
            conn.commit()
            return False
        if not build_shadow_report(conn).get("ok"):
            raise RuntimeError("registry baseline shadow mismatch")
        conn.execute(SCHEMA)
        conn.execute(LEDGER_SQL)
        conn.execute("INSERT INTO "+LEDGER+" VALUES(?,?,?,?)",(VERSION,NAME,CHECKSUM,float(now)))
        _validate_v1(conn)
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("database foreign key violation")
        conn.commit()
        return True
    except BaseException:
        conn.rollback()
        raise


def inspect_activation(conn):
    """Diagnostic only; readiness never confers production authority."""
    from collaboration_migrate import _validate_v1
    if not conn.in_transaction:
        raise RuntimeError("activation inspection requires caller transaction")
    report={"binding_schema_ready":False,"baseline_shadow_ok":False,
            "production_activation":False,"authorization_ready":False}
    try:
        _validate_v1(conn)
        report["binding_schema_ready"]=validate_binding_extension(conn)
        report["baseline_shadow_ok"]=bool(build_shadow_report(conn).get("ok"))
    except (RuntimeError,sqlite3.Error):
        report["reason"]="incompatible_or_missing_schema"
    return report
