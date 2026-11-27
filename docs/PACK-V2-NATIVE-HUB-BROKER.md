# Native task Hub broker boundary

The Swift NativeHubTaskBroker wraps the existing authenticated,
instance-revalidated dedicated Hub transport. The App helper uses
AgentExecutorEnrollment.withExistingToken for the duration of one selected
native task; it closes the broker before returning. The long-lived token
stays in Swift. Node sees only a task-scoped claim token and approved context.

Protocol: one bounded JSON request per exchange, version=1, monotonic
sequence, tool, arguments; response version/sequence/result. Fixed selected
task ID, native provider and opaque UUID. Only task_claim,
collaboration_context_get, run_start, task_complete and task_block allowed.
No generic Hub tools, owner credentials, attachments or API usage from
native child. Claim token and lifecycle checked, bounded frame count and
context reads; no replay of claim/start/terminal mutation. Unknown
terminal result closes the broker; no fallback write. Response allowlist
drops unrelated fields, error details not returned. The child client
accepts injected private exchange only, never loads a token or URL.

This slice implements the in-process broker and App factory using the
real existing Hub transport, plus the Node frame client. It does NOT
implement process launch, private pipe framing/I/O, cancellation and
child lifetime handling, or UI approval interaction yet. Tests use
fixture Hub, not live network; Swift and Node are tested separately.
No production activation, Keychain export, schema changes or new third
party dependencies. The broker is not a sandbox for arbitrary child code;
the trusted bundled child and its provider driver still require review.
Next bind the private bounded child channel and human approval lifecycle,
then recheck scoped context immediately before native send.
