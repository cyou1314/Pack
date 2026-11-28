# Registry v1 main-based port (offline only)

Ported from Foundation ae45ef3da81f457c3ab54ea7a3bcabae451d98ef, retaining the original v1 SQL,
provider labels, backfill functions and v1 checksum inputs. The wrapper runs
only v1; it rejects other versions and is not wired to production startup.

Current Hub initializes legacy members before the optional Registry migration.
The test uses the actual Hub initialization and the optional collaboration
ledger, not only a hand-written minimal Registry fixture. No user database is
used. The collaboration migration ledger remains independent.

Known v1 limitation: gemini-api and volcengine labels map to legacy. Do not
rewrite Foundation v1 to fix this. Introduce a new numbered catalog/backfill
migration after auditing v2 and other reserved versions. No existing login
state creates a connected Agent Connection, Session, or authorization.

The original v1 includes role=member rows (including the owner if present)
for compatibility; this is not execution eligibility. The existing owner and
executor authorization remains required.

The wrapper checks idle connection, enabled foreign keys, ledger version
and checksum, unversioned/partial tables, and integrity before first apply.
It preserves existing rows, restores row factory, rolls back on failure, and
does not perform backup or file ownership checks. It is deliberately only
an integration-copy helper, not a production migration command. The replay
check is not a complete schema comparison or shadow readiness report.

Next: compare Foundation v1 checksum with its canonical source in CI, audit
v2 structured audit compatibility and reserved versions, then add explicit
catalog reconciliation and combined migration preflight. No production
activation until backup/recovery and shadow checks are complete.

Test: `python hub/test_registry_v1_port.py`.
