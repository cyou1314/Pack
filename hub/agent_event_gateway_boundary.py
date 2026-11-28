"""Opt-in Gateway boundary for explicitly bound collaboration executors.

Not a live endpoint. The authenticated actor comes from a trusted host,
never from request JSON. Raw Foundation services remain internal primitives.
"""
import hashlib
import secrets

import agent_event_auth as auth
from agent_events import ingest_agent_event_with_token
from collaboration_connection_binding import resolve_connection


class BoundExecutorPolicy:
    def require_dispatcher(self, conn, actor):
        if not conn.in_transaction or not isinstance(actor, dict):
            raise PermissionError("agent_gateway_forbidden")
        mid = actor.get("id")
        if type(mid) is not int or mid <= 0:
            raise PermissionError("agent_gateway_forbidden")
        row = conn.execute(
            "SELECT name,role,kind,removed_at,access_mode FROM members WHERE id=?",
            (mid,)).fetchone()
        if (not row or row[0] != actor.get("name") or row[1] != "dispatcher"
                or row[2] == "external" or row[3] is not None
                or row[4] not in (None, "full")):
            raise PermissionError("agent_gateway_forbidden")

    def require_agent(self, conn, agent_id):
        if not conn.in_transaction:
            raise RuntimeError("gateway policy requires a transaction")
        row = conn.execute(
            "SELECT member_id,account_ref,connection_id FROM collaboration_connection_bindings "
            "WHERE agent_id=?", (agent_id,)).fetchone()
        if not row:
            raise PermissionError("agent_gateway_binding_unavailable")
        try:
            resolved = resolve_connection(conn, member_id=row[0], account_ref=row[1])
        except ValueError:
            raise PermissionError("agent_gateway_binding_unavailable") from None
        if resolved != (agent_id, row[2]):
            raise PermissionError("agent_gateway_binding_unavailable")


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _new_token():
    return secrets.token_urlsafe(48)


class BoundEventGateway:
    """Trusted host facade: policy enforced in the same DB transaction.

    Only explicitly bound, active collaboration executors are supported.
    No Provider login, task claim, context grant, or session authorization
    is implied by a successful event receipt.
    """
    def __init__(self, context):
        self.context = context
        self.policy = BoundExecutorPolicy()

    def create_pair(self, actor, agent_id, ttl_seconds=600):
        return auth.create_agent_event_pair_code(
            self.context, actor, agent_id, ttl_seconds,
            token_factory=_new_token, hash_token=_hash, policy=self.policy)

    def redeem(self, code):
        return auth.redeem_agent_event_pair_code(
            self.context, code, token_factory=_new_token, hash_token=_hash,
            policy=self.policy)

    def authenticate(self, token):
        return auth.authenticate_agent_event_token(
            self.context, token, hash_token=_hash, policy=self.policy)

    def capabilities(self, token):
        return auth.get_agent_event_capabilities(
            self.context, token, hash_token=_hash, policy=self.policy)

    def ingest(self, token, event):
        return ingest_agent_event_with_token(
            self.context, token, event, hash_token=_hash, policy=self.policy)

    def revoke(self, actor, agent_id, token_id=None):
        # Revocation remains possible even after the binding is disabled.
        return auth.revoke_agent_event_tokens(
            self.context, actor, agent_id, token_id, policy=self.policy)
