"""Unified offline Foundation v1-v7 + Pack catalog v8 migration.

Only for a disposable consistent Hub DB copy. No live startup or activation.
"""
import hashlib
import json
import math
import sqlite3

from registry_v1_port import (
    Migration, _REGISTRY_V1, _apply_registry_v1, _normalized_function_source,
)
from registry_v2_v3_port import (
    _TASK_AUDIT_V2, _TASK_HOTPATH_V3, _apply_task_audit_v2, _check_indexes, _V2_INDEXES,
)
from registry_event_v4_v5_port import (
    _AGENT_EVENT_INBOX_V4, _AGENT_EVENT_GATEWAY_V5, _verify_objects, _normalize,
)
from registry_consumer_v6_port import _AGENT_EVENT_CONSUMER_V6
from registry_session_v7_port import (
    _AGENT_SESSION_PROJECTION_V7, _preflight_agent_session_projection_v7,
    _checksum as foundation_checksum,
)

_CATALOG_V8 = Migration(8, "provider_catalog_alias_reconciliation", ())
_ALIAS_V8 = {"gemini-api": "gemini", "volcengine": "volcengine"}
CHAIN = (_REGISTRY_V1, _TASK_AUDIT_V2, _TASK_HOTPATH_V3,
         _AGENT_EVENT_INBOX_V4, _AGENT_EVENT_GATEWAY_V5,
         _AGENT_EVENT_CONSUMER_V6, _AGENT_SESSION_PROJECTION_V7, _CATALOG_V8)


def _apply_catalog_v8(conn, now):
    """Repair only untouched v1 legacy projections; never bind accounts."""
    existing = conn.execute(
        "SELECT status FROM providers WHERE provider_id='volcengine'"
    ).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO providers(provider_id,display_name,status,created_at,updated_at) "
            "VALUES('volcengine','Volcengine Ark','active',?,?)", (now,now))
    elif existing[0] != "active":
        raise RuntimeError("catalog v8: existing Ark provider is not active")
    rows = conn.execute(
        """SELECT a.agent_id,a.legacy_member_id,a.legacy_provider_label,
                  a.connection_mode,a.status,m.provider,m.role,m.kind
           FROM agents a LEFT JOIN members m ON m.id=a.legacy_member_id
           WHERE a.provider_id='legacy'
             AND lower(trim(a.legacy_provider_label)) IN ('gemini-api','volcengine')
           ORDER BY a.agent_id"""
    ).fetchall()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for row in rows:
        aid, mid, label, mode, state, member_label, role, kind = row
        alias = label.strip().lower()
        if (mid is None or aid != f"legacy-member-{mid}" or role != "member"
                or not isinstance(member_label,str) or member_label.strip().lower() != alias
                or mode is not None or (kind or "").strip().lower() == "external"
                or state not in ("pending","disconnected","removed","legacy_unclassified")):
            raise RuntimeError("catalog v8: ambiguous legacy identity")
        for table in ("agent_connections","agent_sessions","agent_event_inbox",
                      "agent_event_pair_codes","agent_event_tokens",
                      "agent_event_consumer_cursors","agent_event_consumer_leases",
                      "collaboration_connection_bindings"):
            if table in tables and conn.execute(
                    f"SELECT 1 FROM {table} WHERE agent_id=? LIMIT 1",(aid,)).fetchone():
                raise RuntimeError("catalog v8: legacy identity has dependent records")
        target = _ALIAS_V8[alias]
        if not conn.execute(
                "SELECT 1 FROM providers WHERE provider_id=? AND status='active'",(target,)
        ).fetchone():
            raise RuntimeError("catalog v8: target provider unavailable")
        conn.execute("UPDATE agents SET provider_id=?,updated_at=? WHERE agent_id=?",
                     (target,now,aid))


def checksum(m):
    if m.version != 8:
        return foundation_checksum(m)
    payload = {"version":m.version,"name":m.name,"statements":m.statements,
               "aliases":_ALIAS_V8,
               "backfill_source":_normalized_function_source(_apply_catalog_v8)}
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,
                                    separators=(",",":")).encode()).hexdigest()


def _verify_schema(conn, version):
    # Existing base-Hub indexes are allowed before v2/v3. Every new registry
    # object, by contrast, must have its corresponding ledger version.
    session_sql = next(s for s in _REGISTRY_V1.statements
                       if s.startswith("CREATE TABLE agent_sessions "))
    other = tuple(s for s in _REGISTRY_V1.statements if s != session_sql)
    _verify_objects(conn,Migration(1,_REGISTRY_V1.name,other),applied=version>=1)
    row = conn.execute("SELECT type,sql FROM sqlite_master WHERE name='agent_sessions'").fetchone()
    if version==0:
        if row is not None:
            raise RuntimeError("unversioned agent_sessions")
    else:
        if version>=7:
            addition = _AGENT_SESSION_PROJECTION_V7.statements[0].split("ADD COLUMN ",1)[1]
            session_sql = session_sql.rsplit(")",1)[0] + ", " + addition + ")"
        if not row or row[0]!="table" or _normalize(row[1]) != _normalize(session_sql):
            raise RuntimeError("missing or incompatible agent_sessions schema")
    _check_indexes(conn,_V2_INDEXES,required=version>=2)
    _check_indexes(conn,_TASK_HOTPATH_V3.statements,required=version>=3)
    for m in CHAIN[3:6]:
        _verify_objects(conn,m,applied=version>=m.version)
    m = _AGENT_SESSION_PROJECTION_V7
    _verify_objects(conn,Migration(7,m.name,m.statements[1:]),applied=version>=7)
    if version>=8 and not conn.execute(
            "SELECT 1 FROM providers WHERE provider_id='volcengine'").fetchone():
        raise RuntimeError("catalog v8: missing Ark provider")


def apply_offline_chain(conn, *, now):
    """Apply contiguous migrations in one transaction; return new versions.

    This is not a production migration or a backup command. Caller owns the
    disposable DB. Unknown ledger versions, conflicts and invalid DDL abort
    the whole attempt, including any preceding migrations in this call.
    """
    if conn.in_transaction:
        raise RuntimeError("migration requires an idle connection")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0]!=1:
        raise RuntimeError("migration requires foreign keys")
    if type(now) not in (int,float) or not math.isfinite(now):
        raise ValueError("invalid migration timestamp")
    old_factory=conn.row_factory
    conn.row_factory=sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        if [r[0] for r in conn.execute("PRAGMA quick_check")]!=["ok"]:
            raise RuntimeError("database integrity check failed")
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("database foreign key violation")
        conn.execute("""CREATE TABLE IF NOT EXISTS hub_schema_migrations (
            version INTEGER PRIMARY KEY, name TEXT NOT NULL,
            checksum TEXT NOT NULL, applied_at REAL NOT NULL)""")
        rows=conn.execute("SELECT version,name,checksum FROM hub_schema_migrations ORDER BY version").fetchall()
        expected=[(m.version,m.name,checksum(m)) for m in CHAIN]
        if len(rows)>len(CHAIN) or [tuple(r) for r in rows]!=expected[:len(rows)]:
            raise RuntimeError("unsupported or mismatched Hub migration ledger")
        _verify_schema(conn,len(rows))
        applied=[]
        for m in CHAIN[len(rows):]:
            if m.version==7:
                _preflight_agent_session_projection_v7(conn)
            for sql in m.statements:
                conn.execute(sql)
            if m.version==1:
                _apply_registry_v1(conn,float(now))
            elif m.version==2:
                _apply_task_audit_v2(conn)
            elif m.version==8:
                _apply_catalog_v8(conn,float(now))
            conn.execute("INSERT INTO hub_schema_migrations(version,name,checksum,applied_at) VALUES(?,?,?,?)",
                         (m.version,m.name,checksum(m),float(now)))
            applied.append(m.version)
        _verify_schema(conn,len(CHAIN))
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("database foreign key violation")
        conn.commit()
        return applied
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.row_factory=old_factory
