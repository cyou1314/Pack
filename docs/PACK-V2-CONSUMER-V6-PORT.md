# M2 v6 Consumer Lease and ACK port

Source: Foundation PR #49 commit 0ffb4a224c051e46bf62e07c1e3a801c0e30c1cc, including the PR #45
consumer implementation and transaction-scoped ACK/release helpers.

Canonical v6 DDL and checksums are preserved. The offline port validates the
v1-v5 prefix and schema before applying v6 and rejects unversioned or modified
v6 objects. Original service functions are copied unchanged. Tests adapt
the upstream context to the actual main-based Hub plus isolated migration
ports. Production startup and HTTP/MCP routes are untouched.

Consumer identity is internal and code-registered. Ordered per-Consumer /
per-Agent claims, bounded batches, exclusive live leases, expiry, release,
partial contiguous ACK and idempotent completed ACK are supported. The
transaction helpers allow future projection side effects and ACK to commit
together. ACK does not itself perform business projection; callers must
never acknowledge an unprocessed event. Internal projector read access is
not an Agent token or user authorization boundary.

No public consumer endpoint, production migration, scheduler or UI.
No Provider credentials or real Mac state are used. Next: v7 schema and
atomic Session projector from PR #51, then end-to-end recovery testing.
