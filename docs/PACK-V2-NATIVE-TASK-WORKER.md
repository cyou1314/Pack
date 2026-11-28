# Single-task native Worker

native-task-worker.mjs composes the actual Native Collaboration Host,
CollaborationRunner, private Hub client and both approval callbacks.
Exactly one invocation per worker; no task polling or fallback driver.
Check known account and browser markers before claiming, and inspect
their absence after the runner returns. A review receipt with a retained
marker is not a clean exit. Unknown claim/stop/delivery/provider outcome
also exits nonzero. No marker deletion; presence of a new concurrent
owner can conservatively report cleanup unconfirmed. Marker absence
alone is not proof that all browser descendants or remote work stopped.

The fixed developer CLI validates only provider, UUID and task ID.
Its reviewed driver registry is deliberately empty until live provider
surface and identity evidence have been accepted. All current invocations
exit 2 without creating a pipe or claiming a task. No external module,
selector configuration, fixture or credential may be supplied via CLI.
The existing App binding sheet explicitly reports that native execution
is not yet available; no launch button or production path added.

The actual Swift-to-Node integration fixture now uses the Worker, shared
Host, real local identity metadata, account/browser leases, runner,
synthetic browser transport and mock Hub. Tests cover successful review,
denial before run_start, repeated invocation, retained lease on close
failure, existing lease and blocked CLI. Fixture is excluded from payload.
No real Provider request, verified live login or production activation.
No new dependencies. Rebuild candidate manifest for the changed revision.
Next: complete read-only identity observation and reviewed live surface
on Mac; wire fixed bundle launcher and scope-pinned UI only after evidence.
