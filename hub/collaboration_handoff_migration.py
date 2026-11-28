"""Independent compact-handoff extension; stopped, disposable DB copies only.

No startup migration, production activation or changes to frozen v1 checksum.
"""
import hashlib
import math

from registry_ddl_compare import normalize_ddl

TABLE = "collaboration_compact_handoffs"
LEDGER = "pack_collaboration_handoff_migrations"
NAME = "owner_approved_compact_handoff"
SCHEMA = """CREATE TABLE collaboration_compact_handoffs (
    task_id INTEGER PRIMARY KEY REFERENCES collaboration_task_links(task_id) ON DELETE RESTRICT,
    owner_id INTEGER NOT NULL REFERENCES members(id) ON DELETE RESTRICT,
    source_sha256 TEXT NOT NULL,
    text TEXT NOT NULL,
    approved_at REAL NOT NULL
)"""
LEDGER_SQL = """CREATE TABLE pack_collaboration_handoff_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at REAL NOT NULL
)"""
CHECKSUM = hashlib.sha256((SCHEMA+"\n"+LEDGER_SQL).encode("utf-8")).hexdigest()


def validate_handoff_extension(conn):
    def object_for(name):
        return conn.execute("SELECT type,sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
    table, ledger = object_for(TABLE), object_for(LEDGER)
    if table is None and ledger is None:
        return False
    for obj, sql in ((table,SCHEMA),(ledger,LEDGER_SQL)):
        if (not obj or obj[0]!="table" or not obj[1]
                or normalize_ddl(obj[1])!=normalize_ddl(sql)):
            raise RuntimeError("unversioned or incompatible compact handoff extension")
    rows = [tuple(r) for r in conn.execute(
        "SELECT version,name,checksum FROM pack_collaboration_handoff_migrations ORDER BY version")]
    if rows != [(1,NAME,CHECKSUM)]:
        raise RuntimeError("compact handoff migration ledger mismatch")
    return True


def apply_handoff_extension(conn, *, now):
    """Trusted offline caller owns a verified backup; no CLI or live upgrade."""
    from collaboration_migrate import _validate_v1, _validate_hub_schema, _integrity
    if conn.in_transaction or conn.execute("PRAGMA foreign_keys").fetchone()[0]!=1:
        raise RuntimeError("handoff migration requires idle foreign-key-enabled connection")
    if type(now) not in (int,float) or not math.isfinite(now):
        raise ValueError("invalid timestamp")
    try:
        conn.execute("BEGIN IMMEDIATE")
        _validate_hub_schema(conn)
        _validate_v1(conn)
        _integrity(conn)
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("database foreign key violation")
        if validate_handoff_extension(conn):
            conn.commit()
            return False
        conn.execute(SCHEMA)
        conn.execute(LEDGER_SQL)
        conn.execute("INSERT INTO pack_collaboration_handoff_migrations VALUES(1,?,?,?)",
                     (NAME,CHECKSUM,float(now)))
        _validate_v1(conn)
        if not validate_handoff_extension(conn) or conn.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("handoff migration verification failed")
        conn.commit()
        return True
    except BaseException:
        conn.rollback()
        raise
