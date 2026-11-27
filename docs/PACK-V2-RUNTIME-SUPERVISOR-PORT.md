# Foundation Runtime Supervisor port

Source: Foundation PR #51 head 8694e3fc17db4e928f18268a9970e410bfbcdf54. The five core modules
(contract, registry, context package, sensitive field filter and supervisor)
and two upstream regression suites are copied unchanged. The barrel exports
existing Outbox and worker, but deliberately omits the not-yet-ported Codex
App Server adapter. No production adapter is registered by import.

RuntimeSupervisor owns adapter instances, session lifecycle, event
validation, persistence-before-publication, explicit approval callbacks
and cleanup/recovery. CollaborationRunner remains the sole existing
collaboration submission path; ProcessSupervisor remains the bounded
child-process helper. Do not call both sendMessage and runner.execute for
the same task. No new startup, scheduler, direct model calls or live Hub
routes in this slice. The Foundation sendMessage interface is not yet
compatible with the existing one-shot collaboration adapter contract.

Regression includes unchanged upstream tests and a real SQLite Outbox
test with two fixture agents, connection failure, one successful send and
event recovery after closing/reopening storage. The test's shared outbox
does not imply production multi-Agent transport: the delivery worker uses
a token bound to a single Agent. Use per-Agent stores or a reviewed
identity-aware router; never deliver another Agent's events with that token.
Supervisor state and uncommitted event queues are process-local, not durable.
No assertion of exactly-once external model submission, bounded supervisor
shutdown, account provenance, egress authorization or live cancellation.
Sensitive field filter is not a general secret detector; payload contents
still require explicit allowlists and disclosure controls.

Next integrate the existing collaboration runner with a single supervised
execution ownership boundary, then connect per-Agent transport and lifecycle
control. Preserve Muse legacy path until real Mac acceptance.

CI follow-up: run 45934316046 found that Foundation's barrel referenced
getAgentEventCapabilities, which the earlier main-based delivery worker does
not export. Remove only that unsupported export and add an import-surface
regression. The capabilities handshake is not implemented or advertised here;
review Gateway session binding and capability negotiation before porting it.
