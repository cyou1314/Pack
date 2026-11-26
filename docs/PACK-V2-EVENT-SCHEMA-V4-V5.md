# M2 Event schema v4/v5 port

Offline main-based integration, copied from Foundation ae45ef3da81f457c3ab54ea7a3bcabae451d98ef.
Canonical v4/v5 DDL and checksum inputs preserved; no live routes or credentials
issued. v4 stores durable event envelopes with per-Agent event ID uniqueness;
v5 stores pairing-code hashes, token hashes, narrow events:write scope and
pair-attempt metadata. Hash generation, expiry enforcement, authorization,
rate limits, payload validation and conflict handling are service-level
requirements, not guarantees of this schema alone.

Run only on a disposable Hub database copy after exact v1-v3. A valid v4-only
ledger can advance to v5 without deleting inbox rows. Unexpected objects,
mismatched ledger, bad integrity, or changed table/index definitions fail
closed. The port keeps the existing collaboration ledger separate and never
creates a connected Agent or automatically enables dispatch.

Test: python hub/test_registry_event_v4_v5_port.py (actual Hub initialization,
migration replay/resume, uniqueness, foreign keys, scope, tamper detection).
This is not a full canonical checksum cross-check against the upstream
runtime, backup/recovery command, or production migration runner.

Next: review Foundation gateway authentication, event validation and Inbox
service against current main's authorization boundaries; port independently
without exposing routes. Preserve Muse Legacy and existing task paths.
