# Muse legacy Runtime extraction

The actual runtime/runtime.mjs now delegates its existing task, chat seed,
chat send, quota and smoke operations to a flat sibling muse-legacy-adapter.mjs.
No driver script changes. The adapter preserves existing subprocess arguments,
PACK_TASK serialization, script composition order, bounds, cancellation and
result parser. The runtime retains task polling, approvals, memory handling,
receipt recovery, chat watermark and quota scheduling.

Compatibility-only adapter, not the verified native account collaboration
contract. No claim of native account proof or per-action egress enforcement.
Muse's autonomous browsing remains a limitation. No new Provider registration
in RuntimeSupervisor; no automatic retry introduced in this adapter.
Existing legacy Runtime retry behavior is unchanged and still needs separate
review before any production cutover. Keep the existing Engine and account
data. No changes to the Muse4AI bridge or Electron payload. Deployment of a
loose legacy runtime must copy the new sibling module together with runtime.mjs;
no new external dependencies or config keys. Real Mac/ego-browser verification
remains required. Unit tests use injected fixture subprocess and verify exact
composition, all four operations and failure propagation.

Next: inspect the separate Muse4AI bridge and its Engine-specific interfaces;
define verified account provenance and a safe migration path before using
Muse in the modern collaboration runner. Do not silently route other
Provider identities into this adapter.
