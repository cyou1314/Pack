"""Durable, per-Consumer Hub Inbox delivery leases and contiguous ACK cursors.

This module is an internal service boundary. It intentionally exposes no HTTP or
MCP route; callers must hold a ConsumerPrincipal issued by a trusted Hub module.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from typing import Any, Callable


DEFAULT_CLAIM_LIMIT = 100
MAX_CLAIM_LIMIT = 100
DEFAULT_LEASE_SECONDS = 30
MIN_LEASE_SECONDS = 5
MAX_LEASE_SECONDS = 300
DEFAULT_MAX_BATCH_BYTES = 1024 * 1024
MAX_BATCH_BYTES = 4 * 1024 * 1024
ACK_RETRY_WINDOW_SECONDS = 24 * 60 * 60

# Stable internal identities only. Adding a Consumer is a code/security change,
# not something a caller can do by submitting an arbitrary consumer_id.
_INTERNAL_CONSUMER_IDS = frozenset({
    "hub-event-projector",
    "hub-session-projector",
})
_PRINCIPAL_SEAL = object()


# Keep one issuer-owned object per stable Consumer identity. A shared seal alone
# is transferable via dataclasses.replace(), so authorization also checks object
# identity against this private registry.
_PRINCIPAL_INSTANCES: dict[str, "ConsumerPrincipal"] = {}


@dataclass(frozen=True, slots=True)
class ConsumerPrincipal:
    """Opaque internal identity; instances must come from the trusted issuer."""

    consumer_id: str
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._seal is not _PRINCIPAL_SEAL:
            raise ValueError("invalid_consumer_principal")


_PRINCIPAL_INSTANCES.update({
    consumer_id: ConsumerPrincipal(consumer_id, _PRINCIPAL_SEAL)
    for consumer_id in _INTERNAL_CONSUMER_IDS
})


class AgentEventLeaseBusy(RuntimeError):
    """A live lease already owns this Consumer + Agent stream."""


class AgentEventLeaseExpired(RuntimeError):
    """The lease is no longer allowed to advance a consumer cursor."""


class AgentEventLeaseUnauthorized(PermissionError):
    """The principal or lease capability does not authorize this operation."""


class AgentEventBatchTooLarge(ValueError):
    """The next unacknowledged event cannot fit in the requested byte budget."""


def _issue_consumer_principal(consumer_id: str) -> ConsumerPrincipal:
    """Return the issuer-owned principal for a code-registered Consumer."""
    if not isinstance(consumer_id, str) or consumer_id not in _INTERNAL_CONSUMER_IDS:
        raise PermissionError("agent_event_consumer_unauthorized")
    return _PRINCIPAL_INSTANCES[consumer_id]


def _require_principal(principal: ConsumerPrincipal) -> str:
    if (type(principal) is not ConsumerPrincipal
            or principal._seal is not _PRINCIPAL_SEAL
            or principal.consumer_id not in _INTERNAL_CONSUMER_IDS
            or _PRINCIPAL_INSTANCES.get(principal.consumer_id) is not principal):
        raise AgentEventLeaseUnauthorized("agent_event_consumer_unauthorized")
    return principal.consumer_id


def _required_text(value: Any, field_name: str, max_length: int = 160) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_length:
        raise ValueError(f"invalid_{field_name}")
    return value.strip()


def _bounded_int(value: Any, field_name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"invalid_{field_name}")
    return value


def _token_digest(token: str) -> str:
    try:
        encoded = token.encode("ascii")
    except UnicodeEncodeError as error:
        raise AgentEventLeaseUnauthorized("agent_event_lease_unauthorized") from error
    return hashlib.sha256(encoded).hexdigest()


def _valid_token(token: Any) -> str:
    if not isinstance(token, str) or not 32 <= len(token) <= 256:
        raise AgentEventLeaseUnauthorized("agent_event_lease_unauthorized")
    return token


def _authorize_agent(connection, consumer_id: str, agent_id: str) -> None:
    """Apply the internal Consumer allowlist and require a registered Agent.

    The first two Consumers are Hub-owned projectors. They may read registered
    Agent streams; no caller-supplied identity or Agent ownership claim is used.
    """
    if consumer_id not in _INTERNAL_CONSUMER_IDS:
        raise AgentEventLeaseUnauthorized("agent_event_consumer_unauthorized")
    if not connection.execute(
        "SELECT 1 FROM agents WHERE agent_id=?",
        (agent_id,),
    ).fetchone():
        raise ValueError("agent_not_found")


def _lease_token_matches(row, token: str) -> bool:
    return hmac.compare_digest(str(row["lease_token_hash"]), _token_digest(token))


def _cursor_value(connection, consumer_id: str, agent_id: str) -> int:
    row = connection.execute(
        """SELECT acked_inbox_seq FROM agent_event_consumer_cursors
           WHERE consumer_id=? AND agent_id=?""",
        (consumer_id, agent_id),
    ).fetchone()
    return int(row["acked_inbox_seq"]) if row else 0


def claim_agent_events(
    context,
    principal: ConsumerPrincipal,
    agent_id: str,
    *,
    limit: int = DEFAULT_CLAIM_LIMIT,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    max_bytes: int = DEFAULT_MAX_BATCH_BYTES,
    token_factory: Callable[[], str] | None = None,
) -> dict[str, Any]:
    """Claim the next ordered batch for one trusted Consumer + Agent pair."""
    consumer_id = _require_principal(principal)
    agent_id = _required_text(agent_id, "agent_id")
    limit = _bounded_int(limit, "limit", 1, MAX_CLAIM_LIMIT)
    lease_seconds = _bounded_int(
        lease_seconds, "lease_seconds", MIN_LEASE_SECONDS, MAX_LEASE_SECONDS,
    )
    max_bytes = _bounded_int(max_bytes, "max_bytes", 1, MAX_BATCH_BYTES)
    make_token = token_factory or (lambda: secrets.token_urlsafe(32))

    with context.lock:
        connection = context.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            _authorize_agent(connection, consumer_id, agent_id)
            now = float(context.clock())

            cursor = connection.execute(
                """SELECT acked_inbox_seq FROM agent_event_consumer_cursors
                   WHERE consumer_id=? AND agent_id=?""",
                (consumer_id, agent_id),
            ).fetchone()
            if cursor is None:
                connection.execute(
                    """INSERT INTO agent_event_consumer_cursors(
                        consumer_id,agent_id,acked_inbox_seq,updated_at
                    ) VALUES(?,?,0,?)""",
                    (consumer_id, agent_id, now),
                )
                acked_inbox_seq = 0
            else:
                acked_inbox_seq = int(cursor["acked_inbox_seq"])

            active = connection.execute(
                """SELECT lease_id,expires_at FROM agent_event_consumer_leases
                   WHERE consumer_id=? AND agent_id=? AND status='active'""",
                (consumer_id, agent_id),
            ).fetchone()
            if active is not None:
                if float(active["expires_at"]) > now:
                    raise AgentEventLeaseBusy("agent_event_lease_busy")
                connection.execute(
                    """UPDATE agent_event_consumer_leases
                       SET status='expired',closed_at=?
                       WHERE lease_id=? AND status='active'""",
                    (now, active["lease_id"]),
                )

            rows = connection.execute(
                """SELECT inbox_seq,event_json FROM agent_event_inbox
                   WHERE agent_id=? AND inbox_seq>?
                   ORDER BY inbox_seq ASC LIMIT ?""",
                (agent_id, acked_inbox_seq, limit + 1),
            ).fetchall()

            selected = []
            selected_bytes = 0
            for row in rows:
                event_json = row["event_json"]
                if not isinstance(event_json, str):
                    raise RuntimeError("agent_event_inbox_corrupt")
                event_bytes = len(event_json.encode("utf-8"))
                if not selected and event_bytes > max_bytes:
                    raise AgentEventBatchTooLarge("agent_event_exceeds_batch_limit")
                if selected_bytes + event_bytes > max_bytes:
                    break
                try:
                    event = json.loads(event_json)
                except (ValueError, TypeError, RecursionError) as error:
                    raise RuntimeError("agent_event_inbox_corrupt") from error
                selected.append({
                    "inbox_seq": int(row["inbox_seq"]),
                    "event": event,
                })
                selected_bytes += event_bytes

            if not selected:
                connection.commit()
                return {
                    "empty": True,
                    "consumer_id": consumer_id,
                    "agent_id": agent_id,
                    "cursor": acked_inbox_seq,
                    "events": [],
                }

            lease_id = secrets.token_urlsafe(18)
            lease_token = make_token()
            lease_token = _valid_token(lease_token)
            expires_at = now + lease_seconds
            from_inbox_seq = selected[0]["inbox_seq"]
            through_inbox_seq = selected[-1]["inbox_seq"]
            connection.execute(
                """INSERT INTO agent_event_consumer_leases(
                    lease_id,consumer_id,agent_id,lease_token_hash,
                    from_inbox_seq,through_inbox_seq,acked_through_inbox_seq,
                    created_at,expires_at,status
                ) VALUES(?,?,?,?,?,?,?,?,?,'active')""",
                (
                    lease_id, consumer_id, agent_id, _token_digest(lease_token),
                    from_inbox_seq, through_inbox_seq, acked_inbox_seq,
                    now, expires_at,
                ),
            )
            connection.executemany(
                """INSERT INTO agent_event_consumer_lease_items(lease_id,inbox_seq)
                   VALUES(?,?)""",
                [(lease_id, item["inbox_seq"]) for item in selected],
            )
            connection.commit()
            return {
                "empty": False,
                "consumer_id": consumer_id,
                "agent_id": agent_id,
                "cursor": acked_inbox_seq,
                "lease_id": lease_id,
                "lease_token": lease_token,
                "expires_at": expires_at,
                "from_inbox_seq": from_inbox_seq,
                "through_inbox_seq": through_inbox_seq,
                "has_more": len(rows) > len(selected),
                "events": selected,
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def ack_agent_events(
    context,
    principal: ConsumerPrincipal,
    lease_id: str,
    lease_token: str,
    *,
    through_inbox_seq: int,
) -> dict[str, Any]:
    """Advance only the acknowledged prefix of the caller's current lease."""
    consumer_id = _require_principal(principal)
    lease_id = _required_text(lease_id, "lease_id", max_length=128)
    lease_token = _valid_token(lease_token)
    through_inbox_seq = _bounded_int(
        through_inbox_seq, "through_inbox_seq", 1, (1 << 63) - 1,
    )

    with context.lock:
        connection = context.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            lease = connection.execute(
                "SELECT * FROM agent_event_consumer_leases WHERE lease_id=?",
                (lease_id,),
            ).fetchone()
            if lease is None or lease["consumer_id"] != consumer_id:
                raise AgentEventLeaseUnauthorized("agent_event_lease_unauthorized")
            _authorize_agent(connection, consumer_id, lease["agent_id"])
            if not _lease_token_matches(lease, lease_token):
                raise AgentEventLeaseUnauthorized("agent_event_lease_unauthorized")

            now = float(context.clock())
            status = lease["status"]
            acked_through = int(lease["acked_through_inbox_seq"])
            item = connection.execute(
                """SELECT acked_at FROM agent_event_consumer_lease_items
                   WHERE lease_id=? AND inbox_seq=?""",
                (lease_id, through_inbox_seq),
            ).fetchone()

            if status == "completed":
                retry_until = lease["ack_retry_until"]
                if (retry_until is None or now > float(retry_until)
                        or through_inbox_seq > acked_through
                        or item is None or item["acked_at"] is None):
                    raise AgentEventLeaseExpired("agent_event_lease_expired")
                cursor = _cursor_value(connection, consumer_id, lease["agent_id"])
                connection.commit()
                return {
                    "status": "completed",
                    "acked_inbox_seq": cursor,
                    "idempotent": True,
                }

            if status != "active" or float(lease["expires_at"]) <= now:
                raise AgentEventLeaseExpired("agent_event_lease_expired")

            cursor = _cursor_value(connection, consumer_id, lease["agent_id"])
            if cursor != acked_through:
                raise RuntimeError("agent_event_consumer_cursor_mismatch")

            if through_inbox_seq <= acked_through:
                if item is None or item["acked_at"] is None:
                    raise ValueError("agent_event_ack_not_in_lease")
                connection.commit()
                return {
                    "status": "active",
                    "acked_inbox_seq": cursor,
                    "idempotent": True,
                }

            if item is None or through_inbox_seq > int(lease["through_inbox_seq"]):
                raise ValueError("agent_event_ack_not_in_lease")

            connection.execute(
                """UPDATE agent_event_consumer_lease_items SET acked_at=?
                   WHERE lease_id=? AND inbox_seq<=? AND acked_at IS NULL""",
                (now, lease_id, through_inbox_seq),
            )
            connection.execute(
                """UPDATE agent_event_consumer_cursors
                   SET acked_inbox_seq=?,updated_at=?
                   WHERE consumer_id=? AND agent_id=? AND acked_inbox_seq=?""",
                (
                    through_inbox_seq, now, consumer_id, lease["agent_id"],
                    acked_through,
                ),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise RuntimeError("agent_event_consumer_cursor_race")
            remaining = int(connection.execute(
                """SELECT COUNT(*) FROM agent_event_consumer_lease_items
                   WHERE lease_id=? AND acked_at IS NULL""",
                (lease_id,),
            ).fetchone()[0])
            if remaining == 0:
                connection.execute(
                    """UPDATE agent_event_consumer_leases SET
                        acked_through_inbox_seq=?,status='completed',
                        completed_at=?,ack_retry_until=?,closed_at=?
                       WHERE lease_id=? AND status='active'""",
                    (
                        through_inbox_seq, now,
                        now + ACK_RETRY_WINDOW_SECONDS, now, lease_id,
                    ),
                )
                status = "completed"
            else:
                connection.execute(
                    """UPDATE agent_event_consumer_leases
                       SET acked_through_inbox_seq=?
                       WHERE lease_id=? AND status='active'""",
                    (through_inbox_seq, lease_id),
                )
                status = "active"
            connection.commit()
            return {
                "status": status,
                "acked_inbox_seq": through_inbox_seq,
                "idempotent": False,
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def release_agent_event_lease(
    context,
    principal: ConsumerPrincipal,
    lease_id: str,
    lease_token: str,
) -> dict[str, Any]:
    """Release an active lease without advancing its Consumer cursor."""
    consumer_id = _require_principal(principal)
    lease_id = _required_text(lease_id, "lease_id", max_length=128)
    lease_token = _valid_token(lease_token)

    with context.lock:
        connection = context.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            lease = connection.execute(
                "SELECT * FROM agent_event_consumer_leases WHERE lease_id=?",
                (lease_id,),
            ).fetchone()
            if lease is None or lease["consumer_id"] != consumer_id:
                raise AgentEventLeaseUnauthorized("agent_event_lease_unauthorized")
            _authorize_agent(connection, consumer_id, lease["agent_id"])
            if not _lease_token_matches(lease, lease_token):
                raise AgentEventLeaseUnauthorized("agent_event_lease_unauthorized")

            status = lease["status"]
            if status in ("released", "expired"):
                connection.commit()
                return {"status": status, "cursor": _cursor_value(
                    connection, consumer_id, lease["agent_id"],
                )}
            if status == "completed":
                connection.commit()
                return {"status": "completed", "cursor": _cursor_value(
                    connection, consumer_id, lease["agent_id"],
                )}

            now = float(context.clock())
            new_status = "expired" if float(lease["expires_at"]) <= now else "released"
            connection.execute(
                """UPDATE agent_event_consumer_leases
                   SET status=?,closed_at=?
                   WHERE lease_id=? AND status='active'""",
                (new_status, now, lease_id),
            )
            cursor = _cursor_value(connection, consumer_id, lease["agent_id"])
            connection.commit()
            return {"status": new_status, "cursor": cursor}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def get_agent_event_consumer_cursor(
    context,
    principal: ConsumerPrincipal,
    agent_id: str,
) -> dict[str, Any]:
    """Read a Consumer's durable cursor for one authorized Agent."""
    consumer_id = _require_principal(principal)
    agent_id = _required_text(agent_id, "agent_id")
    connection = context.connect()
    try:
        _authorize_agent(connection, consumer_id, agent_id)
        return {
            "consumer_id": consumer_id,
            "agent_id": agent_id,
            "acked_inbox_seq": _cursor_value(connection, consumer_id, agent_id),
        }
    finally:
        connection.close()
