# M2 Runtime Outbox port (standalone)

Source: Foundation PR #43, commit 330a581e7ebdd5cd42e64e22573ffa93b8e4e458. The SQLite event store, delivery
worker, URL policy and their regression tests are ported unchanged. Existing
main-based Runtime and Muse paths are not modified. Existing Runtime CI
already discovers these tests and runs them on Node 22.

The store persists events before returning, uses WAL and synchronous=FULL,
keeps retry state across restart, and blocks later pending events behind the
oldest not-yet-due event. The worker sends one event per runOnce and marks it
delivered only after a valid accepted/duplicate + inboxSeq receipt. Transient
transport failures use persisted retry; authorization failures and invalid
receipts pause rather than discard the pending event. Retry reuses eventId.
The Hub receipt means transport persistence, not consumer ACK or task success.

Security and activation boundaries:
- Use one Worker per Outbox file, with a trusted private directory and
  validated file ownership/path; cross-process leasing is not implemented.
- The URL policy permits HTTPS and loopback HTTP, rejects URL credentials,
  query and fragments; the Worker rejects redirects. A future host must
  pin the configured Hub destination and supply the correct per-event token.
- Only already-authorized, sanitized event envelopes may enter the store.
  Owner-only file modes are not encryption or content redaction. No provider
  tokens, private memory, or unapproved context belong in payloads.
- No production Gateway route, Runtime supervisor scheduling, Keychain
  integration, retention policy, or real network fault injection is enabled.
  Pause state is process-local; retry metadata is durable. An uncertain
  model submission must never be replayed just because its event is retried.
- Do not integrate into the live Runtime until the gateway host, database
  migration, delivery ownership and end-to-end recovery are reviewed.

Next: v6 consumer leases and ACK, v7 Session projection, then offline
transport-to-consumer recovery tests and controlled host integration.
