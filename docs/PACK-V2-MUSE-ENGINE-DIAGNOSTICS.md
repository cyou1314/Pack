# Muse local read-only identity and capability diagnostics

Evidence: MUSE4AI-INTERNALS.md sections 2.2 and 3.1 document the existing
local /v1/status schema, not a verified current installed Engine. Only
documented key scope, exact account alias, ready group and numeric cached
quota are interpreted. No remote account ID, workspace, live login, model,
native memory or capability verification is available from that evidence.

Run explicitly from the installed bridge directory:
    node bridge.mjs --diagnose

Uses the existing local .api-key, requiring owner-only regular file, one
link and bounded size. No key in argv; no Hub initialization, task polling,
submission, cancellation, browser control, account mutation or database
write. Uses only authenticated GET /v1/status on the configured loopback
origin; redirect disabled, 30s existing timeout, response limited to 1 MiB.
Outputs an allowlisted JSON summary, never raw account records, aliases,
credentials, private IDs, plan text or error contents. Exit 0 means a
recognized local credential scope, not verified execution readiness;
exit 2 means unconfirmed/rejected scope, exit 1 configuration/I/O error.

Quota is cached, not a live measurement. Explicit stale=false, valid
observation no older than six hours and not in future, numeric percent
0–100 required. No inferred extra token counts or quota writes to Hub.
Legacy bridge startup log now distinguishes local credential acceptance
from verified remote account identity. Normal legacy execution behavior
otherwise unchanged; invalid CLI arguments do not start the bridge.

Every report retains identityVerified=false, capabilitiesVerified=false,
executionEnabled=false. This is intentionally not enrollment, account
proof or authorization. The actual installed Engine may have different
status fields; validate on Mac without logging raw sensitive data. Do
not infer stable identity from alias, a successful request or a ready
group. Next obtain supported account/workspace and model/memory evidence
from the actual provider surface under explicit user control. Do not
reuse private gateway protocols, bypass paused states or auto-enable.
No new dependencies, production config or credential changes.
