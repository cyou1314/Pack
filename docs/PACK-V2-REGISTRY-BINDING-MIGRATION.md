# Versioned Registry binding extension

Optional empty binding schema on a disposable Hub copy. Requires verified
collaboration v1, v1-v8 Registry shadow parity and foreign-key integrity.
Separate ledger pack_registry_binding_migrations v1 preserves the two
existing migration ledgers and checksums. Existing collaboration validation
recognizes only the exact versioned extension, never an unversioned table.
No binding rows are created automatically. No production migration CLI or
startup wiring. Existing rehearsal still prepares Registry only; extension
requires a separately enabled collaboration v1 on the copy.

Activation inspection is read-only and never grants production authority.
A clean baseline does not establish login, account provenance, room grants
or Runtime dispatch authority. Already evolved identities still need an
explicit lifecycle policy. Existing raw binding and Gateway service tests
remain unchanged. DDL comparison now preserves literal case, whitespace
and quotes instead of flattening them; historical checksums unchanged.

Next: trusted lifecycle transition and per-agent enable policy, then
supervisor wiring and Mac deployment verification. No user data touched.
