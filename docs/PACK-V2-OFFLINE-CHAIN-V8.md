# Pack main-based unified offline migration and Provider v8

This is an atomic v1-v8 runner for disposable copies, not a live startup
migration. It reuses frozen Foundation definitions/checksums v1-v7 and
adds v8 catalog classification. Existing step ports remain historical test
entrypoints; the new runner can resume any valid contiguous ledger prefix.
All new DDL/data/ledger writes in one invocation commit or roll back together.

v8 adds Volcengine Ark as a distinct vendor and repairs v1 legacy projections
for gemini-api -> gemini and volcengine -> volcengine only. It checks the
original member label and v1 identity, rejects external or ambiguous records,
and refuses any existing connection/session/event/token/pair/consumer/binding
dependency (including revoked connections). Existing non-legacy identities
are not rewritten. Status, connection mode, account references and credential
references are not altered; no login or connection is inferred. A conflict
blocks the whole invocation, requiring operator review on a verified copy.
No auto merge, deletion, retry of model calls or production schema activation.

The migration ledger is separate from collaboration migration versions.
A future registry shadow comparator must understand the v8 classification
before production activation. v8 is now assigned in this branch only.
A v8 replay checks schema and provider existence, not mutable catalog contents
or real account provenance. Production backup, read-only preflight, audited
activation and packaging remain future work. Tests cover fresh chain, v1
resume, rollback and conflicts. No real Provider or Mac access.
