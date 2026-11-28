# Registry execution gate on actual Hub task paths

Opt-in process configuration: PACK_REGISTRY_EXECUTION=1. Default off.
For a dedicated executor with an explicit Registry binding, new task_claim,
collaboration_context_get and run_start require versioned control and an
enabled/live binding. Claim validates within the existing BEGIN IMMEDIATE
before reserving collaboration budget; the server-issued opaque claim token
carries the control generation (rgN.<random secret>). Context and run_start
verify the full existing claim token and then its generation in the same
transaction. Pause/resume invalidates pre-pause unstarted claims; old claims
must be cancelled or recovered through existing task lifecycle, not silently
replayed. In-flight reports/completion are deliberately unchanged.

With opt-in off, unbound legacy dedicated executors retain existing behavior.
With opt-in on they require binding. Ordinary non-dedicated legacy members
including Muse stay on their original path. Do not treat this as a complete
production activation switch: migration, verified account provisioning,
trusted control host, cancellation/recovery and provider submission fences
remain incomplete. No new public management route. A task run registration
is not an atomic external model submission. Existing task claim secrets
remain opaque to clients; no client should parse them. Test actual MCP
claim/context/run paths on disposable database; no real Provider calls.

Follow-up: CI run 45929400885 exposed an over-escaped literal dot in the
claim-generation matcher. Correct the expression and directly test valid
and malformed token shapes, retaining the full MCP regression.
