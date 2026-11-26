# M2 Event Gateway service port (offline)

Source: Foundation ae45ef3da81f457c3ab54ea7a3bcabae451d98ef, services/agent_event_auth.py and
services/agent_events.py. The service functions are preserved except for
the strict task-reference import path and flat module layout. The current
main-based Hub is not changed, and no routes are registered.

The test-only context initializes the actual current Hub in a temporary
directory, applies the v1-v5 ports, and opens SQLite with foreign keys on.
The Foundation non-HTTP service tests are retained. The original HTTP
route test is deferred explicitly until route and permission integration;
it is not counted as passing here.

Service behavior includes single-use hashed pairing codes, scoped hashed
Agent tokens, expiry/revocation, throttling, atomic token validation plus
event append, identity binding to the token, duplicate receipt and payload
conflict detection, bounded event envelopes and sensitive-key filtering.
A receipt is only durable transport acceptance, not consumer ACK.

Important boundaries: internal issue/revoke functions trust an actor supplied
by a trusted host; they are NOT user-facing authorization. Before registering
routes, recheck dispatcher role, access mode, removed status, executor
revocation, connection identity, TLS, request-size limits and audit semantics.
Do not rely on sensitive-key filtering as a general secret detector; Runtime
must restrict outbound content. No account credentials or real Provider
calls are used here.

Tests: python hub/test_agent_event_auth_port.py and
python hub/test_agent_event_inbox_port.py. Existing main CI remains intact.
Next: integrate the trusted management boundary and offline Outbox delivery,
then validate the actual routes before opt-in production activation.
