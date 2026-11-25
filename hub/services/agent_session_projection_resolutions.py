"""Audited operator resolution for blocked Session projection events."""
from __future__ import annotations

import json
import uuid
from typing import Any

from services.agent_event_consumers import (
    AgentEventLeaseBusy,
    AgentEventLeaseUnauthorized,
    _authorize_agent,
    _cursor_value,
    _finite_timestamp,
    _require_principal,
    _required_text,
)
from services.agent_sessions import _actor_identity


_PROJECTOR_CONSUMER_ID = "hub-session-projector"
_MAX_RESOLUTION_REASON_LENGTH = 500


def resolve_blocked_agent_event(
    context,
    principal,
    actor: dict[str, Any],
    agent_id: str,
    inbox_seq: int,
    reason: str,
) -> dict[str, Any]:
    """Skip exactly the next blocked Inbox event with an auditable cursor advance.

    A skip is not a Lease ACK. It is a separate, owner / dispatcher-authorized
    resolution that writes the resolution, receipt, Hub audit row and Consumer
    cursor in one transaction. It cannot run while a Lease is active or jump
    over an earlier unacknowledged event.
    """
    consumer_id = _require_principal(principal)
    if consumer_id != _PROJECTOR_CONSUMER_ID:
        raise AgentEventLeaseUnauthorized("agent_event_consumer_unauthorized")

    agent_id = _required_text(agent_id, "agent_id", max_length=160)
    if isinstance(inbox_seq, bool) or not isinstance(inbox_seq, int) or inbox_seq < 1:
        raise ValueError("invalid_inbox_seq")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("invalid_resolution_reason")
    reason = reason.strip()
    if len(reason) > _MAX_RESOLUTION_REASON_LENGTH:
        raise ValueError("invalid_resolution_reason")
    role, actor_name = _actor_identity(actor)
    actor_identity = f"{role}:{actor_name}"

    with context.lock:
        connection = context.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = _finite_timestamp(float(context.clock()))
            _authorize_agent(connection, consumer_id, agent_id)
            cursor = _cursor_value(connection, consumer_id, agent_id)

            receipt = connection.execute(
                """SELECT r.event_id,r.event_type,r.outcome,
                          i.event_id AS inbox_event_id,
                          i.event_type AS inbox_event_type
                   FROM agent_event_projection_receipts r
                   JOIN agent_event_inbox i
                     ON i.inbox_seq=r.inbox_seq AND i.agent_id=r.agent_id
                   WHERE r.consumer_id=? AND r.agent_id=? AND r.inbox_seq=?""",
                (consumer_id, agent_id, inbox_seq),
            ).fetchone()
            if receipt is None:
                raise ValueError("agent_event_projection_receipt_not_found")
            if (
                receipt["event_id"] != receipt["inbox_event_id"]
                or receipt["event_type"] != receipt["inbox_event_type"]
            ):
                raise RuntimeError("agent_event_projection_receipt_conflict")

            existing_resolution = connection.execute(
                """SELECT resolution_id,event_id,action,actor,reason
                   FROM agent_event_projection_resolutions
                   WHERE consumer_id=? AND agent_id=? AND inbox_seq=?""",
                (consumer_id, agent_id, inbox_seq),
            ).fetchone()
            if existing_resolution is not None:
                if (
                    receipt["outcome"] != "skipped"
                    or existing_resolution["event_id"] != receipt["event_id"]
                    or existing_resolution["action"] != "skip"
                    or cursor < inbox_seq
                ):
                    raise RuntimeError("agent_event_projection_resolution_conflict")
                connection.commit()
                return {
                    "status": "skipped",
                    "resolution_id": existing_resolution["resolution_id"],
                    "event_id": receipt["event_id"],
                    "event_type": receipt["event_type"],
                    "acked_inbox_seq": cursor,
                    "idempotent": True,
                }

            if receipt["outcome"] != "blocked":
                raise ValueError("agent_event_projection_receipt_not_blocked")

            active_lease = connection.execute(
                """SELECT lease_id FROM agent_event_consumer_leases
                   WHERE consumer_id=? AND agent_id=? AND status='active'
                   LIMIT 1""",
                (consumer_id, agent_id),
            ).fetchone()
            if active_lease is not None:
                raise AgentEventLeaseBusy("agent_event_lease_busy")

            next_event = connection.execute(
                """SELECT inbox_seq FROM agent_event_inbox
                   WHERE agent_id=? AND inbox_seq>?
                   ORDER BY inbox_seq LIMIT 1""",
                (agent_id, cursor),
            ).fetchone()
            if next_event is None or int(next_event["inbox_seq"]) != inbox_seq:
                raise ValueError("agent_event_projection_resolution_not_next")

            resolution_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO agent_event_projection_resolutions(
                    resolution_id,consumer_id,agent_id,inbox_seq,event_id,
                    action,reason,actor,resolved_at
                ) VALUES(?,?,?,?,?,'skip',?,?,?)""",
                (
                    resolution_id, consumer_id, agent_id, inbox_seq,
                    receipt["event_id"], reason, actor_identity, now,
                ),
            )
            receipt_update = connection.execute(
                """UPDATE agent_event_projection_receipts
                   SET outcome='skipped',reason_code='operator_skipped',
                       attempt_count=attempt_count+1,last_attempt_at=?,projected_at=?
                   WHERE consumer_id=? AND agent_id=? AND inbox_seq=?
                     AND outcome='blocked'""",
                (now, now, consumer_id, agent_id, inbox_seq),
            )
            if receipt_update.rowcount != 1:
                raise RuntimeError("agent_event_projection_receipt_resolution_race")

            cursor_update = connection.execute(
                """UPDATE agent_event_consumer_cursors
                   SET acked_inbox_seq=?,updated_at=?
                   WHERE consumer_id=? AND agent_id=? AND acked_inbox_seq=?""",
                (inbox_seq, now, consumer_id, agent_id, cursor),
            )
            if cursor_update.rowcount != 1:
                raise RuntimeError("agent_event_consumer_cursor_resolution_race")

            detail = json.dumps(
                {
                    "agent_id": agent_id,
                    "inbox_seq": inbox_seq,
                    "event_id": receipt["event_id"],
                    "event_type": receipt["event_type"],
                    "resolution_id": resolution_id,
                    "action": "skip",
                    "reason": reason,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            context.audit(
                connection,
                actor_identity,
                "agent_event_projection.skipped",
                detail,
            )
            connection.commit()
            return {
                "status": "skipped",
                "resolution_id": resolution_id,
                "event_id": receipt["event_id"],
                "event_type": receipt["event_type"],
                "acked_inbox_seq": inbox_seq,
                "idempotent": False,
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
