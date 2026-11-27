# Native task App control components

NativeTaskExecutionController is a single-use MainActor control model:
one pending approval, unique ticket, bounded approval lifetime (default
60 seconds, also limited by context deadline), deny on scope drift,
cancellation or timeout. A stale ticket cannot approve another prompt.
Stop denies pending approval and cancels the local task, not the remote
model or browser. Controller retains only transient approval material.
The broker independently validates task, context, hash, expiry and Hub
identity. The controller is not an authorization substitute.

NativeTaskControlView renders the actual context items and exact outgoing
text, destination, digest, deadline, explicit approve/deny and stop.
It does not start anything on appearance. The App helper
runNativeTaskProcess composes the existing Keychain-backed broker and
private process channel and keeps Hub receipt separate from confirmed
child exit. No generic executable chooser or new launch action is wired
into the user-facing account sheet; a reviewed bundled execution worker
and scope-pinned launch site are still missing. The view is a reusable
compiled component, not a reachable production feature. No fixture
worker exposed to users.

Tests cover ticket correlation, timeout, cancellation, scope and foreign
task denial, and preserving review despite unconfirmed child exit.
CI compiles the UI; manual presentation is not yet verified on Mac.
No new dependencies, database changes, real provider calls or production
activation. Next: explicit worker composition and supervised developer
entry, after review of cancellation and actual identity evidence.
