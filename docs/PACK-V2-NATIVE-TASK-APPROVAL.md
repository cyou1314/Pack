# Native task approvals and authoritative Hub receipt

The task broker now requires two explicit local authorization decisions:
context grant before run_start and exact outgoing text before completion.
The default callback denies. App factory requires the caller's explicit
callback. The Node helper converts existing native Host callbacks into
local broker frames, never Hub tools. The broker validates selected task,
origin, conversation reference, exact SHA-256, role instruction prefix,
and decoded context items against the original Hub response. After
human send approval it reads scoped context again via the authenticated
Hub, checking equality and deadline. Denial, context drift or unknown
readback never grants send. Trusted child code must honor the decision;
this is not browser network isolation or a malicious-child sandbox.
There is still a race between final read and remote send.

Broker outcome is based on observed Hub receipts and approval history:
review, blocked, providerUnknown, stopUnknown, deliveryUnknown, etc.
After send approval the outcome is executionUnconfirmed until a
terminal receipt; this does not establish that a click occurred. Even a
review receipt does not prove browser shutdown. No automatic retry,
lease cleanup, or fake token counts. Child exit is separate.

Tests exercise explicit approvals, denial, stale context and cross-process
fixture with approval frames; no live model call or actual App UI yet.
Next connect transient SwiftUI approval presentation, worker result and
child supervision to an explicitly selected developer task. Real provider
identity observation and reviewed selectors remain blockers.
