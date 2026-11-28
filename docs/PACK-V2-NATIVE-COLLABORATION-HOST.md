# Native collaboration host composition

Extract the ChatGPT-specific host's shared composition into
native-collaboration-host.mjs. Existing ChatGPT entrypoint retains the
same explicit surface/identity callback contract. Gemini and Doubao
can use the shared lifecycle only with a trusted, separately reviewed
Provider page-driver factory. There are no default Gemini/Doubao live
drivers, selectors, identity observers or automatic execution switches.

The common host binds one UUID and exact Provider/mode/model/memory
destination, checks existing enrolled identity metadata, re-reads
profile and proof before execution, uses the same durable account lease
as Swift/login, and opens only the dedicated BrowserSessions context.
An about:blank page may navigate to the fixed Provider origin.
The driver must verify actual identity on every inspect, not merely
return a cached proof. This is a trusted-code contract, not a sandbox
for arbitrary plug-ins. Both approvals remain mandatory; no remote
task chooses a driver or page script. Muse remains on its legacy path.

Tests use injected fixture drivers with real local identity metadata
and account leases, not live Gemini/Doubao pages. They exercise task
claim, profile drift, foreign account, changed identity, approval,
single send, review delivery and lease release. Existing ChatGPT
Chromium smoke still covers real browser operations on a synthetic page.
Update browser candidate packaging allowlist; old manifests are not
valid for the new revision. No new dependencies, schema migration,
Keychain exports, live Hub transport or production activation.
Next: implement instance-pinned dedicated Hub communication with
a trusted Swift credential boundary; no token in argv or disk export.
