# Registry shadow parity gate (offline)

Adapted Foundation shadow report from 8694e3fc17db4e928f18268a9970e410bfbcdf54 to the main-based v1-v8
ledger and v8 alias map. The CLI requires an explicit database path and
opens it read-only. All checks use a coherent read transaction, and restore
the caller's row factory and transaction ownership. Report contains member
IDs, field names, counts and diagnostic codes, not member names, tokens,
credentials, message content or private account context.

Checks cover exact migration ledger, integrity/foreign keys, canonical
schema, legacy mapping, provider classification, display identity, legacy
status and connection mode. Existing legacy Muse remains unclassified or
pending as appropriate. No login or active connection inferred. Strict
baseline parity deliberately reports evolved bound identities as drift;
a later explicit lifecycle policy must distinguish valid transitions.
A separate structural executor inventory is included; missing collaboration
schema does not invalidate legacy parity. Neither report authorizes execution.
The report always states authorization_ready=false and production_activation=false.

The snapshot rehearsal now refuses a successful result if shadow parity
fails. A blocked manifest retains the verified backup; a migrated copy may
exist even when the later parity gate fails. Never promote it automatically.
No live shadow scheduling, dual write, production startup, binding migration,
account login or installer changes. Next: activation gate design and explicit
migration of binding schema with controlled lifecycle transitions.

Run: python hub/agent_registry_shadow.py --db /path/to/migration.rehearsal.db
