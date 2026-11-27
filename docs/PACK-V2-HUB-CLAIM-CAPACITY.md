# Hub-wide Registry claim capacity

Registry opt-in claims now use durable existing task and collaboration link
rows inside the same BEGIN IMMEDIATE transaction, before reserve_claim.
Max 4 active bound tasks per Hub, max 1 per bound member/account. Count
claimed and working regardless of elapsed lease or revoked/paused binding;
existing Hub task lifecycle determines when a slot is released. Review
releases a slot, since Provider delivery has ended from Hub perspective.
Cancelled tasks may still have external work in flight: shared disk account
lease and operator reconciliation remain necessary. This is not a remote
request concurrency guarantee. Ordinary legacy tasks and unbound dedicated
tasks with Registry opt-in off remain outside the limit.

Capacity denial returns task_id, status unavailable, code execution_capacity
or execution_account_busy, without a claim token, before budget reservation.
Node and Swift recognize only this narrow trusted receipt as a definite
non-attempt and allow a later explicit invocation, never automatic retry.
All transport or malformed receipt uncertainty remains fail closed.
No new migration, datastore, credential change, production startup or
automatic task polling. Existing Hub rows already persist attempts, claims,
budget and run outcomes across restarts. The local admission tracking
history is still process-local; no automatic reconciliation of uncertain
external submissions. Fixed capacity 4 is conservative and unbenchmarked.
Tests cover five different bound accounts (native and API), budget
preservation, per-account busy, slot release, separate SQLite connection,
and Node/Swift receipt handling. No external Provider calls.
