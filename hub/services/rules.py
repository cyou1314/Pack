"""Approval and egress rule persistence.

The FastMCP layer in hub.py retains dispatcher authentication, access-mode
enforcement, and the public tool signatures. All writes (including audit)
use a SQLite write reservation, which also serializes independent Hub
processes sharing the same database.
"""
from __future__ import annotations

if __package__ == "services":
    from core.policy import egress_pattern
else:
    from ..core.policy import egress_pattern


def _write(context, operation):
    """Run a rule mutation and its audit as one reserved SQLite transaction."""
    with context.lock:
        conn = context.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = operation(conn)
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def list_approval_rules(context) -> dict:
    conn = context.connect()
    try:
        rows = conn.execute(
            "SELECT id, action, target, effect, source, enabled, created_by,"
            " created_at FROM approval_rules ORDER BY id"
        ).fetchall()
        return {"rules": [dict(row) for row in rows]}
    finally:
        conn.close()


def upsert_approval_rule(context, actor: dict, action: str, target: str,
                         effect: str, source: str = "") -> dict:
    if effect not in ("allow", "block"):
        raise ValueError("effect must be 'allow' or 'block'")
    action, target = action.strip(), target.strip()

    def operation(conn):
        conn.execute(
            "INSERT INTO approval_rules(action,target,effect,source,enabled,"
            "created_by,created_at) VALUES(?,?,?,?,1,?,?)"
            " ON CONFLICT(action,target) DO UPDATE SET"
            " effect=excluded.effect, source=excluded.source, enabled=1",
            (action, target, effect, source, actor["name"], context.clock()),
        )
        row = conn.execute(
            "SELECT id FROM approval_rules WHERE action=? AND target=?",
            (action, target),
        ).fetchone()
        context.audit(
            conn, actor["name"], "rule_upsert", f"{action}->{target} = {effect}",
        )
        return {
            "rule_id": row["id"], "action": action,
            "target": target, "effect": effect,
        }

    return _write(context, operation)


def delete_approval_rule(context, actor: dict, rule_id: int) -> dict:
    def operation(conn):
        cursor = conn.execute(
            "DELETE FROM approval_rules WHERE id=?", (rule_id,)
        )
        if cursor.rowcount != 1:
            raise ValueError(f"no such rule: {rule_id}")
        context.audit(
            conn, actor["name"], "rule_delete", f"rule#{rule_id}",
        )
        return {"deleted": rule_id}

    return _write(context, operation)


def list_egress_rules(context) -> dict:
    conn = context.connect()
    try:
        rows = conn.execute(
            "SELECT id, pattern, effect, source, enabled, created_by,"
            " created_at FROM egress_rules ORDER BY id"
        ).fetchall()
        return {"rules": [dict(row) for row in rows]}
    finally:
        conn.close()


def upsert_egress_rule(context, actor: dict, pattern: str, effect: str,
                       source: str = "") -> dict:
    if effect not in ("allow", "ask"):
        raise ValueError("effect must be 'allow' or 'ask'")
    pattern = egress_pattern(pattern)

    def operation(conn):
        conn.execute(
            "INSERT INTO egress_rules(pattern,effect,source,enabled,"
            "created_by,created_at) VALUES(?,?,?,1,?,?)"
            " ON CONFLICT(pattern) DO UPDATE SET"
            " effect=excluded.effect, source=excluded.source, enabled=1",
            (pattern, effect, source, actor["name"], context.clock()),
        )
        row = conn.execute(
            "SELECT id FROM egress_rules WHERE pattern=?", (pattern,)
        ).fetchone()
        context.audit(
            conn, actor["name"], "egress_rule_upsert",
            f"{pattern} = {effect}",
        )
        return {
            "rule_id": row["id"], "pattern": pattern,
            "effect": effect,
        }

    return _write(context, operation)


def delete_egress_rule(context, actor: dict, rule_id: int) -> dict:
    def operation(conn):
        cursor = conn.execute(
            "DELETE FROM egress_rules WHERE id=?", (rule_id,)
        )
        if cursor.rowcount != 1:
            raise ValueError(f"no such egress rule: {rule_id}")
        context.audit(
            conn, actor["name"], "egress_rule_delete",
            f"egress_rule#{rule_id}",
        )
        return {"deleted": rule_id}

    return _write(context, operation)
