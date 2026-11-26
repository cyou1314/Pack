"""Read-only inventory for the collaboration/Agent Registry integration.

This is a structural diagnostic, NOT an authorization or account binding
mechanism. Never use a candidate to dispatch, attach credentials, or resume
a Provider session. Run against a disposable database copy in a caller-owned
read transaction. No migration, Provider calls or database writes occur here.
"""
import uuid

# Local catalog ID and access kind -> Foundation provider ID and mode.
# Ark remains a distinct provider; do not infer a Doubao account from it.
_PROVIDER_MODE = {
    ("muse", "native"): ("muse", "native_account"),
    ("chatgpt", "native"): ("openai", "native_account"),
    ("openai", "api"): ("openai", "official_api"),
    ("gemini", "native"): ("gemini", "native_account"),
    ("gemini-api", "api"): ("gemini", "official_api"),
    ("doubao", "native"): ("doubao", "native_account"),
    ("volcengine", "api"): ("volcengine", "official_api"),
}

_REQUIRED = {
    "collaboration_executors": {"member_id", "owner_id", "provider",
                                "access_kind", "account_ref", "revoked"},
    "members": {"id", "role", "removed_at"},
    "providers": {"provider_id", "status"},
    "agents": {"agent_id", "provider_id", "legacy_member_id", "status"},
    "agent_connections": {"connection_id", "agent_id", "mode", "status"},
}


def _account_uuid(value):
    if not isinstance(value, str):
        return None
    try:
        normalized = str(uuid.UUID(value))
    except ValueError:
        return None
    return normalized if normalized == value.lower() else None


def inspect_executor_registry(conn):
    """Return member IDs and issue codes only; do not expose account refs.

    All reads share the caller's transaction. A structurally compatible row
    still needs explicit account binding and independent authorization.
    """
    if not conn.in_transaction:
        raise RuntimeError("identity inventory requires a caller-owned transaction")
    missing = []
    for table, columns in _REQUIRED.items():
        actual = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not columns <= actual:
            missing.append(table)
    if missing:
        return {"schema_compatible": False, "missing_or_incompatible": missing,
                "executors": [], "authorization_ready": False}

    rows = conn.execute(
        "SELECT member_id,owner_id,provider,access_kind,account_ref "
        "FROM collaboration_executors WHERE revoked=0 ORDER BY member_id"
    ).fetchall()
    counts = {}
    for row in rows:
        normalized = _account_uuid(row[4])
        if normalized is not None:
            counts[normalized] = counts.get(normalized, 0) + 1

    result = []
    for member_id, owner_id, provider, kind, account_ref in rows:
        issues = []
        normalized = _account_uuid(account_ref)
        if normalized is None:
            issues.append("invalid_account_ref")
        elif counts[normalized] != 1:
            issues.append("duplicate_account_ref")
        expected = _PROVIDER_MODE.get((provider, kind))
        if expected is None:
            issues.append("unsupported_provider_mode")
        member = conn.execute(
            "SELECT role,removed_at FROM members WHERE id=?", (member_id,)
        ).fetchone()
        if not member or tuple(member) != ("member", None):
            issues.append("invalid_executor_member")
        owner = conn.execute(
            "SELECT removed_at FROM members WHERE id=?", (owner_id,)
        ).fetchone()
        if not owner or owner[0] is not None or owner_id == member_id:
            issues.append("invalid_executor_owner")

        agents = conn.execute(
            "SELECT agent_id,provider_id,status FROM agents "
            "WHERE legacy_member_id=?", (member_id,)
        ).fetchall()
        if len(agents) != 1:
            issues.append("missing_agent" if not agents else "ambiguous_agent")
        else:
            agent_id, provider_id, status = agents[0]
            if status not in ("pending", "active", "disconnected"):
                issues.append("inactive_agent")
            if expected and provider_id != expected[0]:
                issues.append("provider_mismatch")
            provider_row = conn.execute(
                "SELECT status FROM providers WHERE provider_id=?", (provider_id,)
            ).fetchone()
            if not provider_row or provider_row[0] != "active":
                issues.append("inactive_provider")
            if expected:
                connections = conn.execute(
                    "SELECT mode FROM agent_connections WHERE agent_id=? "
                    "AND status IN ('pending','connected')", (agent_id,)
                ).fetchall()
                if len(connections) != 1:
                    issues.append("missing_connection" if not connections
                                  else "ambiguous_connection")
                elif connections[0][0] != expected[1]:
                    issues.append("connection_mode_mismatch")

        result.append({"member_id": member_id,
                       "structural_match": not issues,
                       "issues": issues,
                       "explicit_account_binding_required": True})

    return {"schema_compatible": True, "missing_or_incompatible": [],
            "executors": result, "authorization_ready": False}
