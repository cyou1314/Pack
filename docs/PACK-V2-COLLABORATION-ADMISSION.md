# Collaboration execution admission

The existing Node CollaborationRunner now always goes through a bounded
CollaborationExecutionSupervisor before task_claim. It is a separate
one-shot task admission responsibility in agent-core, not the session
RuntimeSupervisor.sendMessage path. No second provider invocation.
Default max concurrent 4, tracked attempts 4096. No queue, no retry, no
automatic eviction; capacity denial occurs before Hub mutation. Share an
explicit supervisor across runners for one Hub identity. Retain attempted
task IDs (including unknown receipts) for the lifetime of that instance.
Stop admission does not cancel in-flight work. Snapshot exposes counts only.

Existing shared disk account leases and Hub transaction fences remain
authoritative across processes. This supervisor is process-local, not
durable; do not restart to clear ambiguity. Node now also quarantines the
account locally on unknown completion receipt, in addition to retaining
the disk lease. Swift remains on its existing actor and shared disk lease;
no IPC bridge or unified cross-language scheduler yet. No new production
entrypoint, automatic scheduling, direct provider login or Mac changes.
The default limits are conservative engineering choices, not measured
throughput targets. A later host must scope task IDs by Hub identity and
manage reconciliation explicitly. Existing tasks are one-shot; retries
require new authorized task identity, not resubmission of the same ID.
