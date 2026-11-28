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

CONTROL_VERSION=2
CONTROL_NAME="per_agent_execution_control"
CONTROL_TABLE="pack_registry_agent_controls"
CONTROL_SQL="""CREATE TABLE pack_registry_agent_controls (
    member_id INTEGER PRIMARY KEY REFERENCES collaboration_connection_bindings(member_id) ON DELETE RESTRICT,
    state TEXT NOT NULL CHECK(state IN ('enabled','paused','revoked')),
    generation INTEGER NOT NULL CHECK(generation BETWEEN 1 AND 9223372036854775807),
    updated_at REAL NOT NULL
)"""
CONTROL_CHECKSUM=hashlib.sha256(CONTROL_SQL.encode()).hexdigest()


def _object(conn,name):
    return conn.execute("SELECT type,sql FROM sqlite_master WHERE name=?",(name,)).fetchone()


def _verify_object(row,sql):
    return bool(row and row[0]=="table" and row[1] and _normalize(row[1])==_normalize(sql))


def validate_binding_extension(conn):
    """Read-only extension check, independent of base collaboration validation."""
    table,ledger=_object(conn,TABLE),_object(conn,LEDGER)
    control=_object(conn,CONTROL_TABLE)
    if table is None and ledger is None and control is None:
        return False
    if not _verify_object(table,SCHEMA) or not _verify_object(ledger,LEDGER_SQL):
        raise RuntimeError("unversioned or incompatible binding extension")
    rows=[tuple(r) for r in conn.execute(
        "SELECT version,name,checksum FROM "+LEDGER+" ORDER BY version")]
    expected=[(VERSION,NAME,CHECKSUM),(CONTROL_VERSION,CONTROL_NAME,CONTROL_CHECKSUM)]
    if rows not in (expected[:1],expected):
        raise RuntimeError("binding extension ledger mismatch")
    if len(rows)==1 and control is not None:
        raise RuntimeError("unversioned agent control table")
    if len(rows)==2 and not _verify_object(control,CONTROL_SQL):
        raise RuntimeError("incompatible agent control table")
    return True


def validate_control_extension(conn):
    if not validate_binding_extension(conn):
        return False
    return _object(conn,CONTROL_TABLE) is not None


def apply_control_extension(conn, *, now):
    """Install default-off execution control on a disposable bound copy."""
    from collaboration_migrate import _validate_v1
    if conn.in_transaction:
        raise RuntimeError("control migration requires idle connection")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0]!=1:
        raise RuntimeError("control migration requires foreign keys")
    if type(now) not in (int,float) or not math.isfinite(now):
        raise ValueError("invalid timestamp")
    try:
        conn.execute("BEGIN IMMEDIATE")
        _validate_v1(conn)
        if not validate_binding_extension(conn):
            raise RuntimeError("binding migration required")
        # Structural readiness only: valid evolved bindings may intentionally
        # differ from the old member projection after the first installation.
        if not build_shadow_report(conn).get("ready"):
            raise RuntimeError("registry structural validation failed")
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("database foreign key violation")
        if validate_control_extension(conn):
            conn.commit()
            return False
        conn.execute(CONTROL_SQL)
        conn.execute("INSERT INTO "+LEDGER+" VALUES(?,?,?,?)",
                     (CONTROL_VERSION,CONTROL_NAME,CONTROL_CHECKSUM,float(now)))
        _validate_v1(conn)
        if not validate_control_extension(conn):
            raise RuntimeError("control extension missing")
        conn.commit()
        return True
    except BaseException:
        conn.rollback()
        raise


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
