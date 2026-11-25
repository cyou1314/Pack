"""Agent-scoped, write-only credentials for the Hub Event Gateway."""
from __future__ import annotations

from typing import Any


PAIR_CODE_TTL_MIN = 60
PAIR_CODE_TTL_MAX = 900
PAIR_CODE_FAIL_LOCK = 5
PAIR_GLOBAL_FAIL_WINDOW = 300
PAIR_GLOBAL_FAIL_MAX = 20
AGENT_EVENT_TOKEN_TTL = 90 * 24 * 60 * 60
PAIR_ATTEMPT_RETENTION = 4 * PAIR_GLOBAL_FAIL_WINDOW
PAIRABLE_AGENT_STATES = frozenset({"pending", "active", "disconnected"})


class AgentPairRateLimited(Exception):
    """The global Agent pairing failure window is exhausted."""


def _required_text(value: Any, field: str, max_length: int = 160) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_length:
        raise ValueError(f"invalid_{field}")
    return value.strip()


def create_agent_event_pair_code(
    context,
    actor: dict[str, Any],
    agent_id: str,
    ttl_seconds: int = 600,
    *,
    token_factory,
    hash_token,
) -> dict[str, Any]:
    """Issue a short-lived, single-use pairing code for one registered Agent."""
    agent_id = _required_text(agent_id, "agent_id")
    if (isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int)
            or not PAIR_CODE_TTL_MIN <= ttl_seconds <= PAIR_CODE_TTL_MAX):
        raise ValueError("invalid_agent_pair_ttl")
    actor_name = _required_text(actor.get("name") if isinstance(actor, dict) else None, "actor")

    code = token_factory()
    if not isinstance(code, str) or not 32 <= len(code) <= 256:
        raise RuntimeError("agent_pair_code_generation_failed")

    with context.lock:
        connection = context.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            agent = connection.execute(
                """SELECT a.agent_id,a.status,p.status AS provider_status
                   FROM agents a JOIN providers p ON p.provider_id=a.provider_id
                   WHERE a.agent_id=?""",
                (agent_id,),
            ).fetchone()
            if not agent:
                raise ValueError("agent_not_found")
            if agent["status"] not in PAIRABLE_AGENT_STATES or agent["provider_status"] != "active":
                raise ValueError("agent_not_pairable")

            now = float(context.clock())
            # Only one unused code can exist per Agent. A new code revokes the
            # previous one, including a locked code, without touching tokens.
            connection.execute(
                """UPDATE agent_event_pair_codes SET revoked_at=?
                   WHERE agent_id=? AND redeemed_at IS NULL AND revoked_at IS NULL""",
                (now, agent_id),
            )
            cursor = connection.execute(
                """INSERT INTO agent_event_pair_codes(
                    agent_id,code_hash,created_by,created_at,expires_at
                ) VALUES(?,?,?,?,?)""",
                (agent_id, hash_token(code), actor_name, now, now + ttl_seconds),
            )
            code_id = int(cursor.lastrowid)
            context.audit(
                connection, actor_name, "agent_event_pair_code_create",
                f"code_id={code_id} agent_id={agent_id} ttl={ttl_seconds}",
            )
            connection.commit()
            return {
                "code_id": code_id,
                "agent_id": agent_id,
                "pair_code": code,
                "expires_at": now + ttl_seconds,
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def redeem_agent_event_pair_code(
    context,
    code: str,
    *,
    token_factory,
    hash_token,
    global_fail_window: int = PAIR_GLOBAL_FAIL_WINDOW,
    global_fail_max: int = PAIR_GLOBAL_FAIL_MAX,
    code_fail_lock: int = PAIR_CODE_FAIL_LOCK,
    token_ttl_seconds: int = AGENT_EVENT_TOKEN_TTL,
) -> dict[str, Any]:
    """Redeem a single-use code and return a write-only Agent event token once."""
    if not isinstance(code, str) or not code.strip() or len(code.strip()) > 256:
        code = ""
    else:
        code = code.strip()

    connection = context.connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        now = float(context.clock())
        connection.execute(
            "DELETE FROM agent_event_pair_attempts WHERE ts<?",
            (now - PAIR_ATTEMPT_RETENTION,),
        )
        failures = int(connection.execute(
            "SELECT COUNT(*) FROM agent_event_pair_attempts WHERE success=0 AND ts>=?",
            (now - global_fail_window,),
        ).fetchone()[0])
        if failures >= global_fail_max:
            context.audit(
                connection, "agent-pair", "agent_event_pair_global_throttle",
                f"fails={failures} window={global_fail_window}s",
            )
            connection.commit()
            raise AgentPairRateLimited("agent_pair_rate_limited")

        row = connection.execute(
            "SELECT * FROM agent_event_pair_codes WHERE code_hash=?",
            (hash_token(code),),
        ).fetchone() if code else None

        def known_fail(reason: str):
            if row:
                fail_count = int(row["fail_count"]) + 1
                locked_at = now if fail_count >= code_fail_lock else row["locked_at"]
                connection.execute(
                    "UPDATE agent_event_pair_codes SET fail_count=?,locked_at=? WHERE code_id=?",
                    (fail_count, locked_at, row["code_id"]),
                )
                code_id = int(row["code_id"])
                agent_id = row["agent_id"]
            else:
                code_id = 0
                agent_id = ""
            connection.execute(
                "INSERT INTO agent_event_pair_attempts(ts,success) VALUES(?,0)",
                (now,),
            )
            context.audit(
                connection, "agent-pair", "agent_event_pair_failed",
                f"code_id={code_id} agent_id={agent_id} reason={reason}",
            )
            connection.commit()
            raise ValueError("invalid_agent_pair_code")

        if not row:
            known_fail("unknown")
        if row["revoked_at"] is not None:
            known_fail("revoked")
        if row["redeemed_at"] is not None:
            known_fail("redeemed")
        if row["locked_at"] is not None:
            known_fail("locked")
        if float(row["expires_at"]) <= now:
            known_fail("expired")

        agent = connection.execute(
            """SELECT a.agent_id,a.provider_id,a.status,p.status AS provider_status
               FROM agents a JOIN providers p ON p.provider_id=a.provider_id
               WHERE a.agent_id=?""",
            (row["agent_id"],),
        ).fetchone()
        if (not agent or agent["status"] not in PAIRABLE_AGENT_STATES
                or agent["provider_status"] != "active"):
            known_fail("agent_unavailable")

        token = token_factory()
        if not isinstance(token, str) or not 32 <= len(token) <= 256:
            raise RuntimeError("agent_event_token_generation_failed")
        expires_at = now + token_ttl_seconds
        token_cursor = connection.execute(
            """INSERT INTO agent_event_tokens(
                agent_id,token_hash,scope,created_by,created_at,expires_at
            ) VALUES(?,?,'events:write','agent-pair',?,?)""",
            (agent["agent_id"], hash_token(token), now, expires_at),
        )
        token_id = int(token_cursor.lastrowid)
        connection.execute(
            "UPDATE agent_event_pair_codes SET redeemed_at=? WHERE code_id=? AND redeemed_at IS NULL",
            (now, row["code_id"]),
        )
        connection.execute(
            "INSERT INTO agent_event_pair_attempts(ts,success) VALUES(?,1)",
            (now,),
        )
        context.audit(
            connection, "agent-pair", "agent_event_token_issue",
            f"token_id={token_id} agent_id={agent['agent_id']} scope=events:write",
        )
        connection.commit()
        return {
            "token_id": token_id,
            "agent_id": agent["agent_id"],
            "provider_id": agent["provider_id"],
            "token": token,
            "scope": "events:write",
            "expires_at": expires_at,
        }
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def authenticate_agent_event_token(context, token: str, *, hash_token) -> dict[str, Any] | None:
    """Resolve a valid Agent-only events:write token; never accepts member tokens."""
    if not isinstance(token, str) or not token or len(token) > 256:
        return None
    connection = context.connect()
    try:
        row = connection.execute(
            """SELECT t.token_id,t.agent_id,t.scope,t.expires_at,
                      a.provider_id,a.status AS agent_status,
                      p.status AS provider_status
               FROM agent_event_tokens t
               JOIN agents a ON a.agent_id=t.agent_id
               JOIN providers p ON p.provider_id=a.provider_id
               WHERE t.token_hash=? AND t.revoked_at IS NULL""",
            (hash_token(token),),
        ).fetchone()
        if not row:
            return None
        now = float(context.clock())
        if float(row["expires_at"]) <= now:
            return None
        if row["scope"] != "events:write":
            return None
        if row["agent_status"] not in PAIRABLE_AGENT_STATES:
            return None
        if row["provider_status"] != "active":
            return None
        return {
            "token_id": int(row["token_id"]),
            "agent_id": row["agent_id"],
            "provider_id": row["provider_id"],
            "scope": row["scope"],
        }
    finally:
        connection.close()


def get_agent_event_capabilities(context, token: str, *, hash_token) -> dict[str, Any]:
    """Return additive event-envelope capabilities for an authenticated Agent."""
    identity = authenticate_agent_event_token(
        context, token, hash_token=hash_token,
    )
    if identity is None:
        raise PermissionError("agent_event_unauthorized")
    return {
        "event_envelope_version": 2,
        "supported_fields": ["hubSessionId"],
        "features": ["hub-session-id-envelope-v1"],
    }


def revoke_agent_event_tokens(
    context,
    actor: dict[str, Any],
    agent_id: str,
    token_id: int | None = None,
) -> dict[str, Any]:
    """Revoke one token or every active event token for an Agent."""
    agent_id = _required_text(agent_id, "agent_id")
    actor_name = _required_text(actor.get("name") if isinstance(actor, dict) else None, "actor")
    if token_id is not None and (
        isinstance(token_id, bool) or not isinstance(token_id, int) or token_id < 1
    ):
        raise ValueError("invalid_agent_event_token_id")

    with context.lock:
        connection = context.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            agent = connection.execute(
                "SELECT agent_id FROM agents WHERE agent_id=?",
                (agent_id,),
            ).fetchone()
            if not agent:
                raise ValueError("agent_not_found")
            now = float(context.clock())
            if token_id is None:
                cursor = connection.execute(
                    """UPDATE agent_event_tokens SET revoked_at=?
                       WHERE agent_id=? AND revoked_at IS NULL""",
                    (now, agent_id),
                )
            else:
                cursor = connection.execute(
                    """UPDATE agent_event_tokens SET revoked_at=?
                       WHERE agent_id=? AND token_id=? AND revoked_at IS NULL""",
                    (now, agent_id, token_id),
                )
                if cursor.rowcount != 1:
                    raise ValueError("agent_event_token_not_active")
            revoked = int(cursor.rowcount)
            context.audit(
                connection, actor_name, "agent_event_token_revoke",
                f"agent_id={agent_id} token_id={token_id or 'all'} count={revoked}",
            )
            connection.commit()
            return {"agent_id": agent_id, "revoked_count": revoked}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
