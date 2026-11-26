# M2 v7 Session projection port

Source: Foundation PR #51 commit 8694e3fc17db4e928f18268a9970e410bfbcdf54. Canonical v7 schema, preflight and
checksum inputs preserved. The standalone session creation/failure service
and atomic projector are ported, with flat-module import adaptation.
The v7 migration validates the preceding ledger and schema, checks duplicate
or noncanonical legacy Provider Session IDs, and never silently repairs them.
Replay checks include the altered v1 sessions table and new projection objects.

The projector commits read-model writes, receipts, contiguous ACK and blocked
lease release together. It uses Hub-issued session bindings, validates final
message byte count/hash, preserves incomplete output explicitly and records
deferred events as ignored. No automatic skip; blocked events are not ACKed.
Session creation helpers require a trusted authenticated caller; do not expose
them directly. A future host must enforce current actor, Agent binding, room
membership and egress authorization. An event receipt is not a model success.

Tests: adapted Foundation session binding and projector tests plus migration
preflight/replay/preservation tests against temporary current Hub databases.
No routes, scheduler, UI integration, production migration or Mac deployment.
Next: offline end-to-end transport/projection recovery, then catalog correction
and controlled host integration. No real Provider calls.
