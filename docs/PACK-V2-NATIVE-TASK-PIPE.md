# Private native child pipe

NativeTaskProcessChannel launches an explicitly trusted program with two
inherited private pipes, no shell, no listener and no credential arguments.
4-byte unsigned big-endian length followed by JSON bytes. Request cap 32 KiB,
response cap 64 KiB. Single in-flight exchange, bounded count, monotonic
pipe deadline, nonblocking POSIX I/O on utility queue, per-descriptor
SIGPIPE suppression on macOS. Child stdout protocol only; stderr discarded.
The broker still validates task, sequence, operation, claim and lifecycle.
Error replies are fixed and do not include raw exception text.

Node native-task-pipe uses an incremental bounded reader and writes one
frame, without network access or retry. EOF/invalid length/partial response
closes the channel. Parent closes both handles and broker on error, then
checks the child's exit for a bounded grace period. It does not kill a
browser tree, release leases, or automatically retry an uncertain task.
Clean child exit alone is not proof of remote execution success.

CI runs a real Node fixture child from Swift through the existing task broker
with a fixture Hub, plus malformed frame and Node split/oversize/EOF tests.
The fixture is not included in the staged candidate. New pipe source is
included in the exact payload allowlist. No runtime dependency added.
No actual native execution CLI, UI approval frames, production launch,
real account or network Hub test in this slice. The pipe deadline is not
a hard cancellation of in-flight Hub calls or a remote Provider request;
their transport timeout and eventual result reconciliation remain required.
Parent task cancellation is checked between exchanges; a pipe wait may
continue until deadline. No guarantee against malicious trusted child code.
Next: integrate approval and completion status frames, explicit task worker
and supervised cancellation before enabling any developer App action.
