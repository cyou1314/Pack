# M2 cross-runtime recovery regression

The test runs the real Node 22 SQLite Outbox and delivery Worker in separate
processes against a loopback-only Python HTTP fixture calling BoundEventGateway
on a temporary current Hub database with v1-v7 and the explicit binding table.
No production HTTP route is enabled. Tokens pass through subprocess stdin,
not command-line arguments or logs. No external network or Provider calls.

Faults: commit-before-response-loss, Outbox reopen/retry with same event ID,
revoked-token pause and fresh-token replay, injected ACK failure rollback,
lease expiry/reclaim, stale lease rejection, completed projection replay.
Assertions cover durable Inbox count, FIFO order, session binding, final text,
Run state, receipt count and cursor. SQLite sequence gaps are allowed.

This does not simulate power loss, kill -9 during a transaction, production
routing/authentication/TLS, real account identity, supervisor scheduling,
or Mac deployment. The HTTP handler and actor setup are fixtures only.
The production migration and Provider catalog correction remain separate.

Run: python hub/test_event_recovery_e2e.py (Node 22 + Hub CI dependencies).
