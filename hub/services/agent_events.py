"""Durable, idempotent ingestion primitives for Runtime Agent events.

Transaction ownership belongs to the Hub request/service caller. Ingest and any
related audit/state changes must use the same SQLite connection and commit.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any

from core.task_refs import SQLITE_INT_MAX


_EVENT_TYPES = frozenset({
    "connection.changed",
    "session.created",
    "message.delta",
    "message.completed",
    "run.started",
    "run.progress",
    "run.completed",
    "run.failed",
    "run.interrupted",
    "tool.started",
    "tool.completed",
    "approval.requested",
    "usage.updated",
})
_EVENT_FIELDS = frozenset({
    "eventId", "eventType", "providerId", "agentId", "sequence",
    "occurredAt", "payload", "roomId", "sessionId", "runId",
})
_SENSITIVE_KEYS = frozenset({
    "apikey", "xapikey", "accesstoken", "refreshtoken", "oauthtoken",
    "idtoken", "authtoken", "bearertoken", "token", "authorization",
    "authheader", "password", "cookie", "cookies", "cookiejar",
    "sessioncookie", "setcookie", "nativememory", "privatememory",
    "privatecontext", "credential", "credentials", "clientsecret",
    "secretkey", "signingkey", "sshprivatekey", "privatekey", "secret",
})
_MAX_EVENT_BYTES = 256 * 1024
_MAX_EVENT_DEPTH = 24
_MAX_TEXT = 160


def _text(value: Any, field: str, limit: int = _MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f"invalid_{field}")
    return value.strip()


def _safe_payload(value: Any, *, depth: int = 0) -> None:
    if depth > _MAX_EVENT_DEPTH:
        raise ValueError("event_payload_too_deep")
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ValueError("invalid_event_payload_key")
            normalized = key.replace("-", "").replace("_", "").lower()
            if normalized in _SENSITIVE_KEYS:
                raise ValueError("sensitive_event_field_forbidden")
            _safe_payload(nested, depth=depth + 1)
    elif isinstance(value, list):
        for nested in value:
            _safe_payload(nested, depth=depth + 1)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("invalid_event_number")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("invalid_event_payload_value")


def _normalize_event(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise ValueError("invalid_agent_event")
    unknown = set(event) - _EVENT_FIELDS
    if unknown:
        raise ValueError("unknown_agent_event_fields")

    normalized = {
        "eventId": _text(event.get("eventId"), "event_id"),
        "eventType": _text(event.get("eventType"), "event_type", 64),
        "providerId": _text(event.get("providerId"), "provider_id", 128),
        "agentId": _text(event.get("agentId"), "agent_id"),
        "occurredAt": _text(event.get("occurredAt"), "occurred_at", 64),
        "sequence": event.get("sequence", 0),
        "payload": event.get("payload", {}),
    }
    if normalized["eventType"] not in _EVENT_TYPES:
        raise ValueError("unsupported_event_type")
    sequence = normalized["sequence"]
    if (isinstance(sequence, bool) or not isinstance(sequence, int)
            or sequence < 0 or sequence > SQLITE_INT_MAX):
        raise ValueError("invalid_event_sequence")
    if not isinstance(normalized["payload"], dict):
        raise ValueError("invalid_event_payload")
    try:
        parsed = datetime.fromisoformat(normalized["occurredAt"].replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("invalid_occurred_at") from error
    if parsed.tzinfo is None:
        raise ValueError("occurred_at_timezone_required")

    for field in ("roomId", "sessionId", "runId"):
        if field in event and event[field] is not None:
            normalized[field] = _text(event[field], field, 160)

    _safe_payload(normalized["payload"])
    try:
        encoded = json.dumps(
            normalized, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError("invalid_agent_event_json") from error
    if len(encoded.encode("utf-8")) > _MAX_EVENT_BYTES:
        raise ValueError("agent_event_too_large")
    return normalized


def append_agent_event(connection, event: dict[str, Any], *, received_at: float) -> dict[str, Any]:
    """Persist one event idempotently; do not commit the caller's transaction.

    A repeated (agentId, eventId) with identical content is a successful
    duplicate. Reusing that key for different content is rejected.
    """
    if not connection.in_transaction:
        raise RuntimeError("agent_event_transaction_required")
    normalized = _normalize_event(event)
    if isinstance(received_at, bool) or not isinstance(received_at, (int, float)):
        raise ValueError("invalid_received_at")
    if not math.isfinite(float(received_at)):
        raise ValueError("invalid_received_at")

    agent = connection.execute(
        "SELECT provider_id FROM agents WHERE agent_id=?",
        (normalized["agentId"],),
    ).fetchone()
    if not agent:
        raise ValueError("agent_not_found")
    if agent["provider_id"] != normalized["providerId"]:
        raise ValueError("agent_provider_mismatch")

    event_json = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )
    cursor = connection.execute(
        """INSERT INTO agent_event_inbox(
            agent_id,event_id,provider_id,event_type,event_sequence,
            occurred_at,event_json,received_at
        ) VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(agent_id,event_id) DO NOTHING""",
        (
            normalized["agentId"], normalized["eventId"], normalized["providerId"],
            normalized["eventType"], normalized["sequence"],
            normalized["occurredAt"], event_json, float(received_at),
        ),
    )
    inserted = cursor.rowcount == 1
    row = connection.execute(
        "SELECT inbox_seq,event_json FROM agent_event_inbox "
        "WHERE agent_id=? AND event_id=?",
        (normalized["agentId"], normalized["eventId"]),
    ).fetchone()
    if not row:
        raise RuntimeError("agent_event_inbox_write_missing")
    if row["event_json"] != event_json:
        raise ValueError("event_id_payload_conflict")
    return {"inserted": inserted, "inboxSeq": int(row["inbox_seq"])}


class AgentEventRateLimited(Exception):
    """The Agent exceeded the Event Gateway ingestion rate limit."""


def ingest_agent_event_with_token(
    context,
    token: str,
    event: dict[str, Any],
    *,
    hash_token,
    max_events_per_minute: int = 1200,
) -> dict[str, Any]:
    """Authenticate an Agent token and atomically append one scoped event.

    Agent/provider identity is derived from the token's registered Agent row.
    The request may repeat those fields only when they exactly match; it cannot
    select or impersonate another Agent. Duplicate events do not consume the
    new-event rate budget.
    """
    if not isinstance(token, str) or not token or len(token) > 256:
        raise PermissionError("agent_event_unauthorized")
    if (isinstance(max_events_per_minute, bool)
            or not isinstance(max_events_per_minute, int)
            or max_events_per_minute < 1 or max_events_per_minute > 10000):
        raise ValueError("invalid_agent_event_rate_limit")
    if not isinstance(event, dict):
        raise ValueError("invalid_agent_event")

    with context.lock:
        connection = context.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = float(context.clock())
            auth = connection.execute(
                """SELECT t.token_id,t.agent_id,t.scope,t.expires_at,
                          a.provider_id,a.status AS agent_status,
                          p.status AS provider_status
                   FROM agent_event_tokens t
                   JOIN agents a ON a.agent_id=t.agent_id
                   JOIN providers p ON p.provider_id=a.provider_id
                   WHERE t.token_hash=? AND t.revoked_at IS NULL""",
                (hash_token(token),),
            ).fetchone()
            if (
                not auth
                or float(auth["expires_at"]) <= now
                or auth["scope"] != "events:write"
                or auth["agent_status"] not in {"pending", "active", "disconnected"}
                or auth["provider_status"] != "active"
            ):
                raise PermissionError("agent_event_unauthorized")

            agent_id = auth["agent_id"]
            provider_id = auth["provider_id"]
            if "agentId" in event and event["agentId"] != agent_id:
                raise ValueError("agent_identity_mismatch")
            if "providerId" in event and event["providerId"] != provider_id:
                raise ValueError("agent_provider_mismatch")
            bound_event = {**event, "agentId": agent_id, "providerId": provider_id}

            receipt = append_agent_event(
                connection, bound_event, received_at=now,
            )
            if receipt["inserted"]:
                recent_count = int(connection.execute(
                    """SELECT COUNT(*) FROM agent_event_inbox
                       WHERE agent_id=? AND received_at>=?""",
                    (agent_id, now - 60.0),
                ).fetchone()[0])
                if recent_count > max_events_per_minute:
                    raise AgentEventRateLimited("agent_event_rate_limited")

            connection.execute(
                "UPDATE agent_event_tokens SET last_used_at=? WHERE token_id=?",
                (now, auth["token_id"]),
            )
            connection.commit()
            return {
                "accepted": True,
                "duplicate": not receipt["inserted"],
                "inboxSeq": receipt["inboxSeq"],
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
