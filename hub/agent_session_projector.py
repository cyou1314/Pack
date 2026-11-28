"""Atomic projection of Hub Inbox events into Hub-owned Agent Session read models.

This service is intentionally internal. It owns no HTTP/MCP route and never
opens a second connection for projection, receipt updates, or Consumer ACK.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from typing import Any

from agent_event_consumers import (
    AgentEventLeaseExpired,
    AgentEventLeaseUnauthorized,
    _authorize_agent,
    _cursor_value,
    _finite_timestamp,
    _lease_token_matches,
    _require_principal,
    _required_text,
    _valid_token,
    ack_agent_events_in_transaction,
    release_agent_event_lease_in_transaction,
)


_PROJECTOR_CONSUMER_ID = "hub-session-projector"
_MAX_MESSAGE_DELTA_UTF16_UNITS = 4000
_MAX_MESSAGE_BYTES = 4 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_SUPPORTED_SESSION_EVENTS = frozenset({
    "session.created",
    "message.delta",
    "message.completed",
    "run.started",
    "run.completed",
    "run.failed",
    "run.interrupted",
})
_DEFERRED_EVENT_TYPES = frozenset({
    "connection.changed",
    "run.progress",
    "tool.started",
    "tool.completed",
    "approval.requested",
    "usage.updated",
})


class _ProjectionBlocked(Exception):
    """An event is validly stored in Inbox but cannot be safely projected."""

    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def _block(reason_code: str) -> None:
    raise _ProjectionBlocked(reason_code)


def _event_text(value: Any, field_name: str, limit: int = 160) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        _block(f"invalid_{field_name}")
    return value.strip()


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        _block("invalid_event_payload")
    return payload


def _touch_session(
    connection,
    session_id: str,
    inbox_seq: int,
    received_at: float,
    *,
    status: str | None = None,
) -> None:
    if status is None:
        cursor = connection.execute(
            """UPDATE agent_sessions
               SET last_inbox_seq=MAX(last_inbox_seq,?),updated_at=?
               WHERE session_id=?""",
            (inbox_seq, received_at, session_id),
        )
    else:
        cursor = connection.execute(
            """UPDATE agent_sessions
               SET status=?,last_inbox_seq=MAX(last_inbox_seq,?),updated_at=?
               WHERE session_id=?""",
            (status, inbox_seq, received_at, session_id),
        )
    if cursor.rowcount != 1:
        _block("hub_session_not_found")


def _load_bound_session(connection, agent_id: str, event: dict[str, Any]):
    hub_session_id = _event_text(event.get("hubSessionId"), "hub_session_id")
    provider_session_id = _event_text(event.get("sessionId"), "session_id")
    row = connection.execute(
        """SELECT session_id,agent_id,provider_session_id,status
           FROM agent_sessions
           WHERE session_id=? AND agent_id=?""",
        (hub_session_id, agent_id),
    ).fetchone()
    if row is None:
        _block("hub_session_not_found")
    if row["provider_session_id"] is None:
        _block("session_not_bound")
    if row["provider_session_id"] != provider_session_id:
        _block("provider_session_binding_mismatch")
    if row["status"] in {"failed", "completed", "closed", "archived", "disconnected"}:
        _block("session_not_active")
    return row


def _apply_session_created(
    connection,
    agent_id: str,
    event: dict[str, Any],
    inbox_seq: int,
    received_at: float,
) -> tuple[str, str | None]:
    hub_session_id = _event_text(event.get("hubSessionId"), "hub_session_id")
    provider_session_id = _event_text(event.get("sessionId"), "session_id")
    payload = _event_payload(event)
    payload_provider_id = _event_text(
        payload.get("providerSessionId"), "provider_session_id",
    )
    if payload_provider_id != provider_session_id:
        _block("provider_session_id_payload_mismatch")

    session = connection.execute(
        """SELECT session_id,agent_id,provider_session_id,status
           FROM agent_sessions
           WHERE session_id=? AND agent_id=?""",
        (hub_session_id, agent_id),
    ).fetchone()
    if session is None:
        _block("hub_session_not_found")

    if session["status"] == "failed":
        if session["provider_session_id"] in (None, provider_session_id):
            return "ignored", "late_session_created_after_failure"
        _block("session_binding_conflict")

    if (
        session["status"] == "creating"
        and session["provider_session_id"] is None
    ):
        try:
            cursor = connection.execute(
                """UPDATE agent_sessions
                   SET provider_session_id=?,status='active',
                       last_inbox_seq=MAX(last_inbox_seq,?),updated_at=?
                   WHERE session_id=? AND agent_id=?
                     AND status='creating' AND provider_session_id IS NULL""",
                (
                    provider_session_id, inbox_seq, received_at,
                    hub_session_id, agent_id,
                ),
            )
        except sqlite3.IntegrityError:
            _block("provider_session_id_conflict")
        if cursor.rowcount != 1:
            _block("session_binding_conflict")
        return "applied", None

    if (
        session["status"] in {"active", "interrupted"}
        and session["provider_session_id"] == provider_session_id
    ):
        _touch_session(
            connection, hub_session_id, inbox_seq, received_at, status="active",
        )
        return "applied", None

    _block("session_binding_conflict")


def _apply_run_event(
    connection,
    agent_id: str,
    event: dict[str, Any],
    event_type: str,
    inbox_seq: int,
    received_at: float,
) -> tuple[str, str | None]:
    session = _load_bound_session(connection, agent_id, event)
    run_id = _event_text(event.get("runId"), "run_id")
    existing = connection.execute(
        """SELECT run_record_id,status,started_at,ended_at
           FROM agent_session_runs
           WHERE session_id=? AND provider_run_id=?""",
        (session["session_id"], run_id),
    ).fetchone()

    if event_type == "run.started":
        if existing is None:
            connection.execute(
                """INSERT INTO agent_session_runs(
                    run_record_id,session_id,provider_run_id,status,started_at,
                    updated_at,ended_at,first_inbox_seq,last_inbox_seq
                ) VALUES(?,?,?,'running',?,?,NULL,?,?)""",
                (
                    str(uuid.uuid4()), session["session_id"], run_id,
                    received_at, received_at, inbox_seq, inbox_seq,
                ),
            )
        elif existing["status"] == "running":
            connection.execute(
                """UPDATE agent_session_runs
                   SET updated_at=?,last_inbox_seq=?
                   WHERE run_record_id=?""",
                (received_at, inbox_seq, existing["run_record_id"]),
            )
        else:
            _touch_session(
                connection, session["session_id"], inbox_seq, received_at,
            )
            return "ignored", "late_run_started_after_terminal"
        _touch_session(
            connection, session["session_id"], inbox_seq, received_at,
            status="active",
        )
        return "applied", None

    terminal_status = {
        "run.completed": "completed",
        "run.failed": "failed",
        "run.interrupted": "interrupted",
    }[event_type]
    if existing is None:
        connection.execute(
            """INSERT INTO agent_session_runs(
                run_record_id,session_id,provider_run_id,status,started_at,
                updated_at,ended_at,first_inbox_seq,last_inbox_seq
            ) VALUES(?,?,?,?,NULL,?,?,?,?)""",
            (
                str(uuid.uuid4()), session["session_id"], run_id,
                terminal_status, received_at, received_at, inbox_seq, inbox_seq,
            ),
        )
    elif existing["status"] == "running":
        connection.execute(
            """UPDATE agent_session_runs
               SET status=?,updated_at=?,ended_at=?,last_inbox_seq=?
               WHERE run_record_id=?""",
            (
                terminal_status, received_at, received_at, inbox_seq,
                existing["run_record_id"],
            ),
        )
    elif existing["status"] == terminal_status:
        connection.execute(
            """UPDATE agent_session_runs
               SET updated_at=?,last_inbox_seq=?
               WHERE run_record_id=?""",
            (received_at, inbox_seq, existing["run_record_id"]),
        )
    else:
        _block("run_terminal_conflict")

    message_status = {
        "completed": "incomplete",
        "failed": "failed",
        "interrupted": "interrupted",
    }[terminal_status]
    connection.execute(
        """UPDATE agent_session_messages
           SET status=?,updated_at=?,last_inbox_seq=?
           WHERE session_id=? AND provider_run_id=? AND status='streaming'""",
        (
            message_status, received_at, inbox_seq,
            session["session_id"], run_id,
        ),
    )
    _touch_session(
        connection,
        session["session_id"],
        inbox_seq,
        received_at,
        status="interrupted" if terminal_status == "interrupted" else None,
    )
    return "applied", None


def _message_run_id(event: dict[str, Any]) -> str | None:
    value = event.get("runId")
    return None if value is None else _event_text(value, "run_id")


def _check_message_run_binding(existing, run_id: str | None) -> str | None:
    stored = existing["provider_run_id"]
    if stored is not None and run_id is not None and stored != run_id:
        _block("message_run_binding_conflict")
    return stored if stored is not None else run_id


def _apply_message_delta(
    connection,
    agent_id: str,
    event: dict[str, Any],
    inbox_seq: int,
    received_at: float,
) -> tuple[str, str | None]:
    session = _load_bound_session(connection, agent_id, event)
    if session["status"] != "active":
        _block("session_not_active")
    payload = _event_payload(event)
    item_id = _event_text(payload.get("itemId"), "item_id")
    delta = payload.get("delta")
    if not isinstance(delta, str):
        _block("invalid_message_delta")
    try:
        delta_bytes = delta.encode("utf-8")
        delta_units = len(delta.encode("utf-16-le")) // 2
    except UnicodeError:
        _block("invalid_message_delta")
    if delta_units > _MAX_MESSAGE_DELTA_UTF16_UNITS:
        _block("message_delta_too_large")

    run_id = _message_run_id(event)
    existing = connection.execute(
        """SELECT message_id,provider_run_id,content,status
           FROM agent_session_messages
           WHERE session_id=? AND provider_message_id=?""",
        (session["session_id"], item_id),
    ).fetchone()
    if existing is None:
        if len(delta_bytes) > _MAX_MESSAGE_BYTES:
            _block("message_content_too_large")
        connection.execute(
            """INSERT INTO agent_session_messages(
                message_id,session_id,provider_message_id,provider_run_id,
                content,status,phase,first_inbox_seq,last_inbox_seq,
                created_at,updated_at,completed_at
            ) VALUES(?,?,?,?,?,'streaming',NULL,?,?,?, ?,NULL)""",
            (
                str(uuid.uuid4()), session["session_id"], item_id, run_id,
                delta, inbox_seq, inbox_seq, received_at, received_at,
            ),
        )
    else:
        if existing["status"] != "streaming":
            _block("message_delta_after_terminal")
        bound_run_id = _check_message_run_binding(existing, run_id)
        content = str(existing["content"]) + delta
        try:
            content_bytes = content.encode("utf-8")
        except UnicodeError:
            _block("invalid_message_delta")
        if len(content_bytes) > _MAX_MESSAGE_BYTES:
            _block("message_content_too_large")
        connection.execute(
            """UPDATE agent_session_messages
               SET provider_run_id=?,content=?,last_inbox_seq=?,updated_at=?
               WHERE message_id=?""",
            (
                bound_run_id, content, inbox_seq, received_at,
                existing["message_id"],
            ),
        )
    _touch_session(connection, session["session_id"], inbox_seq, received_at)
    return "applied", None


def _completion_metadata(payload: dict[str, Any]) -> tuple[
    bool, bool, int | None, str | None, str | None
]:
    text_available = payload.get("textAvailable")
    text_included = payload.get("textIncluded")
    if not isinstance(text_available, bool) or not isinstance(text_included, bool):
        _block("invalid_message_completion_metadata")
    if text_included and not text_available:
        _block("invalid_message_completion_metadata")

    byte_length = payload.get("textByteLength")
    digest = payload.get("textSha256")
    text_value = payload.get("text")
    if text_available:
        if (
            isinstance(byte_length, bool)
            or not isinstance(byte_length, int)
            or byte_length < 0
            or byte_length > 2_147_483_647
            or (text_included and byte_length > _MAX_MESSAGE_BYTES)
            or not isinstance(digest, str)
            or not _SHA256_RE.fullmatch(digest)
        ):
            _block("invalid_message_completion_metadata")
        if text_included:
            if not isinstance(text_value, str):
                _block("invalid_message_completion_text")
            try:
                encoded = text_value.encode("utf-8")
            except UnicodeError:
                _block("invalid_message_completion_text")
            if len(encoded) != byte_length:
                _block("message_completion_length_mismatch")
            if hashlib.sha256(encoded).hexdigest() != digest:
                _block("message_completion_hash_mismatch")
        elif "text" in payload:
            _block("invalid_message_completion_metadata")
    else:
        if text_included or byte_length is not None or digest is not None or "text" in payload:
            _block("invalid_message_completion_metadata")
    phase_value = payload.get("phase")
    if phase_value is None or phase_value == "":
        phase = None
    elif isinstance(phase_value, str) and len(phase_value) <= 64:
        phase = phase_value.strip() or None
    else:
        _block("invalid_message_phase")
    return text_available, text_included, byte_length, digest, phase


def _apply_message_completed(
    connection,
    agent_id: str,
    event: dict[str, Any],
    inbox_seq: int,
    received_at: float,
) -> tuple[str, str | None]:
    session = _load_bound_session(connection, agent_id, event)
    if session["status"] != "active":
        _block("session_not_active")
    payload = _event_payload(event)
    item_id = _event_text(payload.get("itemId"), "item_id")
    text_available, text_included, byte_length, digest, phase = _completion_metadata(payload)
    run_id = _message_run_id(event)
    existing = connection.execute(
        """SELECT message_id,provider_run_id,content,status,first_inbox_seq
           FROM agent_session_messages
           WHERE session_id=? AND provider_message_id=?""",
        (session["session_id"], item_id),
    ).fetchone()
    if existing is not None and existing["status"] in {"failed", "interrupted"}:
        _block("message_completion_after_terminal")

    if existing is None:
        first_inbox_seq = inbox_seq
        prior_content = ""
        bound_run_id = run_id
    else:
        first_inbox_seq = int(existing["first_inbox_seq"])
        prior_content = str(existing["content"])
        bound_run_id = _check_message_run_binding(existing, run_id)

    if text_included:
        final_content = payload["text"]
        final_status = "completed"
        reason_code = None
    elif not text_available:
        final_content = prior_content
        final_status = "incomplete"
        reason_code = "completion_text_unavailable"
    elif byte_length > _MAX_MESSAGE_BYTES:
        final_content = prior_content
        final_status = "incomplete"
        reason_code = "completion_text_too_large"
    else:
        try:
            prior_bytes = prior_content.encode("utf-8")
        except UnicodeError:
            _block("invalid_stored_message_content")
        matches = (
            len(prior_bytes) == byte_length
            and hashlib.sha256(prior_bytes).hexdigest() == digest
        )
        final_content = prior_content
        final_status = "completed" if matches else "incomplete"
        reason_code = None if matches else "completion_text_mismatch"

    if existing is not None and existing["status"] == "completed":
        if (
            text_available
            and not text_included
            and final_status != "completed"
        ):
            _block("message_completion_conflict")
        if final_status == "completed" and final_content != prior_content:
            _block("message_completion_conflict")
        final_content = prior_content
        final_status = "completed"
        reason_code = None

    completed_at = received_at if final_status == "completed" else None
    if existing is None:
        connection.execute(
            """INSERT INTO agent_session_messages(
                message_id,session_id,provider_message_id,provider_run_id,
                content,status,phase,first_inbox_seq,last_inbox_seq,
                created_at,updated_at,completed_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid.uuid4()), session["session_id"], item_id, bound_run_id,
                final_content, final_status, phase, first_inbox_seq, inbox_seq,
                received_at, received_at, completed_at,
            ),
        )
    else:
        connection.execute(
            """UPDATE agent_session_messages
               SET provider_run_id=?,content=?,status=?,phase=?,
                   last_inbox_seq=?,updated_at=?,completed_at=?
               WHERE message_id=?""",
            (
                bound_run_id, final_content, final_status, phase,
                inbox_seq, received_at, completed_at, existing["message_id"],
            ),
        )
    _touch_session(connection, session["session_id"], inbox_seq, received_at)
    return "applied", reason_code


def _apply_event(
    connection,
    agent_id: str,
    event: dict[str, Any],
    event_type: str,
    inbox_seq: int,
    received_at: float,
) -> tuple[str, str | None]:
    if event_type in _DEFERRED_EVENT_TYPES:
        return "ignored", "deferred_event_type"
    if event_type not in _SUPPORTED_SESSION_EVENTS:
        _block("unknown_event_type")
    if event.get("hubSessionId") is None:
        return "ignored", "legacy_unbound_session"
    if event_type == "session.created":
        return _apply_session_created(
            connection, agent_id, event, inbox_seq, received_at,
        )
    if event_type in {"message.delta", "message.completed"}:
        if event_type == "message.delta":
            return _apply_message_delta(
                connection, agent_id, event, inbox_seq, received_at,
            )
        return _apply_message_completed(
            connection, agent_id, event, inbox_seq, received_at,
        )
    return _apply_run_event(
        connection, agent_id, event, event_type, inbox_seq, received_at,
    )


def _write_projection_receipt(
    connection,
    consumer_id: str,
    agent_id: str,
    item,
    existing,
    outcome: str,
    reason_code: str | None,
    now: float,
) -> None:
    projected_at = None if outcome == "blocked" else now
    if existing is None:
        connection.execute(
            """INSERT INTO agent_event_projection_receipts(
                consumer_id,agent_id,inbox_seq,event_id,event_type,outcome,
                reason_code,attempt_count,first_attempt_at,last_attempt_at,projected_at
            ) VALUES(?,?,?,?,?,?,?,1,?,?,?)""",
            (
                consumer_id, agent_id, item["inbox_seq"], item["event_id"],
                item["event_type"], outcome, reason_code, now, now, projected_at,
            ),
        )
        return
    if existing["event_id"] != item["event_id"] or existing["event_type"] != item["event_type"]:
        raise RuntimeError("agent_event_projection_receipt_conflict")
    if existing["outcome"] != "blocked":
        return
    connection.execute(
        """UPDATE agent_event_projection_receipts
           SET outcome=?,reason_code=?,attempt_count=?,last_attempt_at=?,projected_at=?
           WHERE consumer_id=? AND agent_id=? AND inbox_seq=?""",
        (
            outcome, reason_code, int(existing["attempt_count"]) + 1,
            now, projected_at, consumer_id, agent_id, item["inbox_seq"],
        ),
    )


def process_agent_event_lease(
    context,
    principal,
    lease_id: str,
    lease_token: str,
) -> dict[str, Any]:
    """Project one active Lease and ACK only its successfully processed prefix.

    Projection writes, receipt outcomes, cursor advancement, and Lease release
    share one BEGIN IMMEDIATE transaction. The first blocked event is recorded
    but never ACKed; later items in that Lease are left untouched.
    """
    consumer_id = _require_principal(principal)
    if consumer_id != _PROJECTOR_CONSUMER_ID:
        raise AgentEventLeaseUnauthorized("agent_event_consumer_unauthorized")
    lease_id = _required_text(lease_id, "lease_id", max_length=128)
    lease_token = _valid_token(lease_token)

    with context.lock:
        connection = context.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = _finite_timestamp(float(context.clock()))
            lease = connection.execute(
                "SELECT * FROM agent_event_consumer_leases WHERE lease_id=?",
                (lease_id,),
            ).fetchone()
            if lease is None or lease["consumer_id"] != consumer_id:
                raise AgentEventLeaseUnauthorized("agent_event_lease_unauthorized")
            agent_id = str(lease["agent_id"])
            _authorize_agent(connection, consumer_id, agent_id)
            if not _lease_token_matches(lease, lease_token):
                raise AgentEventLeaseUnauthorized("agent_event_lease_unauthorized")

            cursor = _cursor_value(connection, consumer_id, agent_id)
            if lease["status"] == "completed":
                ack_result = ack_agent_events_in_transaction(
                    connection,
                    principal,
                    lease_id,
                    lease_token,
                    through_inbox_seq=int(lease["acked_through_inbox_seq"]),
                    now=now,
                )
                connection.commit()
                return {
                    "status": "completed",
                    "acked_inbox_seq": int(ack_result["acked_inbox_seq"]),
                    "processed_count": 0,
                    "idempotent": True,
                }
            if lease["status"] != "active" or float(lease["expires_at"]) <= now:
                raise AgentEventLeaseExpired("agent_event_lease_expired")
            if cursor != int(lease["acked_through_inbox_seq"]):
                raise RuntimeError("agent_event_consumer_cursor_mismatch")

            items = connection.execute(
                """SELECT li.inbox_seq,i.event_id,i.event_type,i.agent_id,
                          i.provider_id,i.event_sequence,i.occurred_at,
                          i.event_json,i.received_at
                   FROM agent_event_consumer_lease_items li
                   JOIN agent_event_inbox i ON i.inbox_seq=li.inbox_seq
                   WHERE li.lease_id=? AND li.acked_at IS NULL
                   ORDER BY li.inbox_seq""",
                (lease_id,),
            ).fetchall()
            if not items:
                released = release_agent_event_lease_in_transaction(
                    connection, principal, lease_id, lease_token, now=now,
                )
                connection.commit()
                return {
                    "status": "empty",
                    "acked_inbox_seq": int(released["cursor"]),
                    "processed_count": 0,
                }

            processed_count = 0
            last_processed_seq: int | None = None
            blocked: dict[str, Any] | None = None
            for item in items:
                inbox_seq = int(item["inbox_seq"])
                if (
                    item["agent_id"] != agent_id
                    or inbox_seq < int(lease["from_inbox_seq"])
                    or inbox_seq > int(lease["through_inbox_seq"])
                ):
                    raise RuntimeError("agent_event_lease_item_out_of_range")
                existing = connection.execute(
                    """SELECT event_id,event_type,outcome,attempt_count
                       FROM agent_event_projection_receipts
                       WHERE consumer_id=? AND agent_id=? AND inbox_seq=?""",
                    (consumer_id, agent_id, inbox_seq),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["event_id"] != item["event_id"]
                        or existing["event_type"] != item["event_type"]
                    ):
                        raise RuntimeError("agent_event_projection_receipt_conflict")
                    if existing["outcome"] in {"applied", "ignored", "skipped"}:
                        processed_count += 1
                        last_processed_seq = inbox_seq
                        continue

                try:
                    event = json.loads(item["event_json"])
                    if not isinstance(event, dict):
                        _block("invalid_inbox_event_json")
                    if (
                        event.get("eventId") != item["event_id"]
                        or event.get("eventType") != item["event_type"]
                        or event.get("agentId") != agent_id
                        or event.get("providerId") != item["provider_id"]
                        or event.get("sequence") != int(item["event_sequence"])
                        or event.get("occurredAt") != item["occurred_at"]
                    ):
                        _block("inbox_event_envelope_mismatch")
                    received_at = _finite_timestamp(item["received_at"])
                    outcome, reason_code = _apply_event(
                        connection, agent_id, event, item["event_type"],
                        inbox_seq, received_at,
                    )
                except _ProjectionBlocked as error:
                    outcome = "blocked"
                    reason_code = error.reason_code
                except (ValueError, TypeError, RecursionError, UnicodeError):
                    outcome = "blocked"
                    reason_code = "invalid_inbox_event"

                _write_projection_receipt(
                    connection, consumer_id, agent_id, item, existing,
                    outcome, reason_code, now,
                )
                if outcome == "blocked":
                    blocked = {
                        "inbox_seq": inbox_seq,
                        "reason_code": reason_code,
                    }
                    break
                processed_count += 1
                last_processed_seq = inbox_seq

            if blocked is not None:
                if last_processed_seq is not None:
                    ack_result = ack_agent_events_in_transaction(
                        connection,
                        principal,
                        lease_id,
                        lease_token,
                        through_inbox_seq=last_processed_seq,
                        now=now,
                    )
                    acked_inbox_seq = int(ack_result["acked_inbox_seq"])
                else:
                    acked_inbox_seq = cursor
                release_agent_event_lease_in_transaction(
                    connection, principal, lease_id, lease_token, now=now,
                )
                connection.commit()
                return {
                    "status": "blocked",
                    "acked_inbox_seq": acked_inbox_seq,
                    "processed_count": processed_count,
                    "blocked_inbox_seq": blocked["inbox_seq"],
                    "reason_code": blocked["reason_code"],
                }

            if last_processed_seq is None:
                released = release_agent_event_lease_in_transaction(
                    connection, principal, lease_id, lease_token, now=now,
                )
                connection.commit()
                return {
                    "status": "empty",
                    "acked_inbox_seq": int(released["cursor"]),
                    "processed_count": processed_count,
                }

            ack_result = ack_agent_events_in_transaction(
                connection,
                principal,
                lease_id,
                lease_token,
                through_inbox_seq=last_processed_seq,
                now=now,
            )
            connection.commit()
            return {
                "status": ack_result["status"],
                "acked_inbox_seq": int(ack_result["acked_inbox_seq"]),
                "processed_count": processed_count,
                "idempotent": bool(ack_result["idempotent"]),
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
