# Explicit Agent Connection binding (experimental)

This slice extends the read-only identity inventory with a proposed additive
binding table and transaction-scoped helpers. It does not install a migration,
expose a Hub tool, change existing dispatch, or verify real Provider logins.

- The owner and dedicated executor are revalidated using the existing
  collaboration context authorization helpers.
- Bind an explicit local profile UUID, legacy member, Agent and connected
  Connection. Provider and mode must match; the provider and Agent must be
  active. A matching registry row is never proof of account provenance.
- A unique mapping is immutable; exact replay is idempotent. Revocation keeps
  a tombstone. Replacement or account reuse is intentionally not implemented.
- Re-resolve against current executor, owner, Agent, provider and connection
  states inside the caller's transaction. No cached positive result is valid
  for future requests.
- Foreign keys must be enabled; the caller owns the write transaction and
  rollback. The helpers do not commit.
- A trusted future host must independently verify account provenance, enforce
  room/task and scope checks, and audit binding/revocation. No production code
  calls this module. The proposed table is only created in synthetic tests.
- Muse Legacy is unchanged. New Muse provisioning is not enabled.

Run `python hub/test_collaboration_connection_binding.py`. Fixtures exercise
the existing collaboration ledger plus a minimal Registry schema. They are
not a substitute for a full combined migration test.

Next: land the compatible Registry migration and backfill on the main-based
integration branch, review the identity binding schema and lifecycle, and
only then wire audited APIs under explicit opt-in.
