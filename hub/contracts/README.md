# Hub contract snapshots

These files are generated from the running Hub registration and initializer. They
are compatibility guards, not hand-written API definitions.

- `mcp-tools.json`: the 75 registered FastMCP tools, their descriptions, input
  JSON Schemas, and output JSON Schemas.
- `sqlite-fresh.json`: the SQLite schema produced by initializing an empty DB.
- `sqlite-legacy-migrated.json`: the resulting schema after upgrading the
  deliberately small pre-migration fixture in `legacy-v0.sql`, plus assertions
  that existing member, room, message, task, and transfer data survives.

## Updating snapshots

After an intentional tool or database contract change:

1. Review the implementation and migration behavior.
2. Run `python hub/export_contracts.py --write`.
3. Review the complete JSON diff; do not accept unrelated schema churn.
4. Commit the updated snapshots with the implementation and migration tests.

CI runs `python hub/export_contracts.py --check` and never rewrites baselines.
The exporter uses a disposable copy of `hub.py` and `hub/core`; it does not
create or modify the developer's production database or dispatcher token.
