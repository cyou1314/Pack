# Per-Agent execution control (offline)

Binding extension ledger v2 adds an empty, default-off execution-control table.
No changes to the v1 checksum, Foundation ledger or collaboration v1 checksum.
The caller must own an immediate transaction and authenticate the owner.
Enabling requires an existing active explicit binding and a trusted host
must independently verify account provenance. No implicit login or binding.
Missing control row denies execution. Global opt-in is a strict caller
boolean defaulting false; it is NOT a production configuration switch yet.

Pause fences new execution checks but preserves authenticated event delivery
for prior work. Resume increments generation; a stale generation cannot
pass a fenced execution check. Disconnect invalidates the live binding check.
Permanent revocation tombstones the existing binding, preventing Gateway
auth and event delivery, and cannot be undone by enable. No credential
deletion or Provider logout. Existing in-flight model requests are not
cancelled; no automatic replay, no new task claim or Provider dispatch.
State transitions and audit rows share the caller's transaction.

The structural migration accepts evolved identity drift but never repairs it.
Production lifecycle registration, atomic task claim plus generation fence,
persistent startup configuration and supervisor cancellation are still needed.
No production route, migration CLI, Muse change or Mac deployment.
