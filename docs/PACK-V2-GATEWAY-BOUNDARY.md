# Bound Event Gateway boundary (offline)

This opt-in facade reuses Foundation services and the explicit collaboration
connection binding. Policy checks run inside the same SQLite transaction as
pair creation, redemption, token validation and ingestion. Management checks
use the current dispatcher row (full access, not removed or external);
the actor must have been authenticated by the future host. Never accept
actor identity from client JSON. Unbound Registry-only Agents are denied
by this particular facade. Legacy Muse is not migrated or enrolled.

Revoked executor or binding, disabled Agent/provider, removed owner, or
disconnected/mismatched Connection prevents new pair redemption and event
acceptance, including duplicate receipts. Management revocation remains
available even if a binding is unavailable. A transient reconnect to the
same non-revoked identity can restore access; permanent token revocation
is separate. No session, task or egress permission is granted by events:write.

Foundation raw service functions remain internal and keep their original
behavior without an explicit policy, for isolated upstream regression tests.
Do not expose those functions directly. No routes, production migration,
real Provider credentials, or automatic activation in this slice.
The fixture explicitly creates the experimental binding table; no production
migration installs it yet. HTTP authentication/TLS/body limits, audited
binding lifecycle and session authorization remain future integration work.

Test: python hub/test_agent_event_gateway_boundary.py.
