# Node / Swift collaboration admission parity

Swift API host now uses actor-based task admission before any Hub operation.
Defaults mirror Node (4 active, 4096 tracked attempts, no eviction or retry).
The existing host-local executing flag still serializes a single host.
A shared admission actor survives recreation of manual API hosts via the
process-owned AgentAccountManager, keyed by the verified Hub instance ID
from InstanceBoundCollaborationHub. Up to 32 instance scopes are retained;
unverified compatibility callers receive host-local admission only.

Counts use the existing Node outcome vocabulary. Swift unavailable is
released only because its existing executeOnce returns that result before
task_claim; all claimed/unknown attempts are retained. Same-task replay
never implicitly resumes a previous claim. The App still requires a human
selection and exact outgoing request approval. No key leaves Swift.

This is parity across two process-local implementations, not a global
cross-language concurrency limit. Hub claim/fence and shared account disk
lease remain the cross-process authority. No automatic scheduling, durable
attempt ledger, remote cancellation or restart reconciliation in this slice.
No changes to Muse or existing Provider implementations. Regression covers
shared actors across hosts, capacity, unknown outcomes and no second claim.
