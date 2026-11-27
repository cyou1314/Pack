# Candidate ChatGPT dedicated collaboration host

The trusted developer composition entrypoint chatgpt-collaboration-host.mjs
binds a single local ChatGPT UUID to the existing profile resolver,
enrolled identity proof, isolated BrowserSessions, AccountExecutionLeases,
ChatGPT DOM driver, native adapter and one-shot CollaborationRunner.
Requires a trusted authenticated Hub transport pinned to that account and
instance; no automatic retry, remote fallback, default selector contract
or live identity observer. Requires both explicit approval callbacks.
No CLI or App execution activation, no automatic polling. The local proof
must already exist; actual identity is rechecked by the driver under both
leases. Only the one-shot runner calls the Provider. The session-oriented
RuntimeSupervisor is not a second submission path.

Browser startup may navigate an about:blank page to the fixed origin;
other pages are not silently replaced to satisfy identity checks. Multiple
pages fail closed. Unknown browser launch now retains the outer account
lease as well as any inner recovery marker; known pre-launch failure
permits release. Uncertain send/delivery retains existing quarantine.

Development payload had a missing transitive dependency after the runner
began importing collaboration-execution-supervisor.mjs. Update the exact
allowlist and staging script and import the candidate host in the relocated
CI smoke. Old candidate manifests must be rebuilt for the new revision.
No new third-party dependency or production DMG activation.

Tests: missing proof/foreign provider/approval callbacks, launch ambiguity,
full routed synthetic Chromium task claim through result review and
lease release, manifest dependency checks. No real provider requests.
Trusted selectors and account/workspace/model/memory observation remain
Mac acceptance blockers. The injected Hub transport is not a real host
implementation, and the fixture identity is not a real account.
