"""Hub-owned Agent Session creation and lifecycle services.

Session identity and Room ownership are created by the Hub control plane. Agent
Event payloads are never allowed to create or change this binding.
"""
from __future__ import annotations

import re
import uuid
from typing import Any


_ALLOWED_SESSION_CREATORS = frozenset({"dispatcher", "owner"})
_REASON_RE = re.compile(r"^[a-z0-9_]{1,80}$")


def _actor_identity(actor: Any) -> tuple[str, str]:
    if not isinstance(actor, dict):
        raise PermissionError("agent_session_forbidden")
    role = actor.get("role")
    name = actor.get("name")
    if role not in _ALLOWED_SESSION_CREATORS:
        raise PermissionError("agent_session_forbidden")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        raise PermissionError("agent_session_forbidden")
    return role, name.strip()


def _required_text(value: Any, field: str, limit: int = 160) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f"invalid_{field}")
    return value.strip()


def create_agent_session(
    context,
    actor: dict[str, Any],
    agent_id: str,
    room_name: str,
) -> dict[str, Any]:
    """Create a Hub-issued Session binding in the creating state.

    The caller must pass an actor already authenticated by Hub. This service
    deliberately accepts a Room name, resolves its numeric database ID itself,
    and never accepts a Room ID supplied by an Agent Event.
    """
    _, actor_name = _actor_identity(actor)
    agent_id = _required_text(agent_id, "agent_id")
    room_name = _required_text(room_name, "room_name", 40)

    with context.lock:
        connection = context.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            agent = connection.execute(
                """SELECT a.agent_id,a.status,p.status AS provider_status
                   FROM agents a
                   JOIN providers p ON p.provider_id=a.provider_id
                   WHERE a.agent_id=?""",
                (agent_id,),
            ).fetchone()
            if agent is None:
                raise ValueError("agent_not_found")
            if agent["status"] != "active":
                raise ValueError("agent_not_active")
            if agent["provider_status"] != "active":
                raise ValueError("agent_provider_not_active")

            room = connection.execute(
                "SELECT id FROM rooms WHERE name=?",
                (room_name,),
            ).fetchone()
            if room is None:
                raise ValueError("room_not_found")

            now = float(context.clock())
            hub_session_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO agent_sessions(
                    session_id,agent_id,room_id,provider_session_id,status,
                    last_event_sequence,created_at,updated_at,last_used_at
                ) VALUES(?,?,?,NULL,'creating',0,?,?,NULL)""",
                (hub_session_id, agent_id, int(room["id"]), now, now),
            )
            context.audit(
                connection,
                actor_name,
                "agent_session_create",
                f"session={hub_session_id} agent={agent_id} room_id={int(room['id'])}",
            )
            connection.commit()
            return {
                "hub_session_id": hub_session_id,
                "agent_id": agent_id,
                "room_id": int(room["id"]),
                "room": room_name,
                "status": "creating",
                "created_at": now,
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def fail_agent_session_creation(
    context,
    hub_session_id: str,
    reason_code: str,
) -> dict[str, Any]:
    """Fence a timed-out create operation without overriding a successful bind.

    Only a Session still in creating state with no Provider Session ID can be
    transitioned to failed. A late session.created event must then be ignored
    by the projector rather than reviving this row.
    """
    hub_session_id = _required_text(hub_session_id, "hub_session_id")
    if not isinstance(reason_code, str) or not _REASON_RE.fullmatch(reason_code):
        raise ValueError("invalid_reason_code")

    with context.lock:
        connection = context.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT session_id,agent_id,status,provider_session_id
                   FROM agent_sessions WHERE session_id=?""",
                (hub_session_id,),
            ).fetchone()
            if row is None:
                raise ValueError("agent_session_not_found")

            changed = False
            now = float(context.clock())
            if row["status"] == "creating" and row["provider_session_id"] is None:
                cursor = connection.execute(
                    """UPDATE agent_sessions
                       SET status='failed',updated_at=?
                       WHERE session_id=? AND status='creating'
                         AND provider_session_id IS NULL""",
                    (now, hub_session_id),
                )
                changed = cursor.rowcount == 1
                if changed:
                    context.audit(
                        connection,
                        "hub-session-manager",
                        "agent_session_create_failed",
                        f"session={hub_session_id} agent={row['agent_id']} reason={reason_code}",
                    )

            current = connection.execute(
                """SELECT status,provider_session_id,updated_at
                   FROM agent_sessions WHERE session_id=?""",
                (hub_session_id,),
            ).fetchone()
            connection.commit()
            return {
                "hub_session_id": hub_session_id,
                "status": current["status"],
                "provider_session_id": current["provider_session_id"],
                "changed": changed,
                "updated_at": current["updated_at"],
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
