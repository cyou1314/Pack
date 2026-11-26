# Pack Browser Driver (experimental)

Independent, opt-in adapter. Uses Playwright Core 1.65.1 (Apache-2.0) and its matching Chromium, with one persistent profile per Native Account UUID. Does not change the current Muse Engine, install browser dependencies on an end user's Mac, or connect to a real provider. Normal mode uses a visible browser; CI explicitly enables headless mode on a loopback fixture.

The caller must provision a dedicated private (0700), user-owned Pack root, NOT an existing everyday browser data directory. A directory lease rejects duplicate opens across cooperating managers. Ambiguous launch/close failures retain the lease for recovery: do not remove it until the owning process/browser has been confirmed stopped. No account login, credential export, network automation policy, cancellation of active page tasks, process-tree supervision, IPC or App integration yet. Raw context is an internal trusted-code interface, not an Agent tool surface.

The package version is fixed, but registry integrity metadata could not be retrieved here; no package-lock or browser artifact hash is fabricated. The separate experimental CI job installs the exact version and runs a local Chromium smoke test. Release packaging remains blocked until the dependency lock, verified downloads, licensing notices and full lifecycle acceptance are complete.

Run mock tests: node --test runtime/browser-driver/sessions.test.mjs.
Real Chromium smoke (separate dev environment): cd runtime/browser-driver; npm install --ignore-scripts --no-audit --no-fund; node node_modules/playwright-core/cli.js install chromium; node --test chromium.smoke.mjs.

## Native collaboration contract

`native-account-adapter.mjs` reuses BrowserSessions and requires a trusted Provider-specific page driver, independently enrolled account proof, verified model/memory state and explicit per-send authorization. It does not implement any live website selectors, login, challenge bypass or account-proof enrollment. A fresh conversation is required; one send only; correlated completed assistant reply only; unknown outcomes retain account execution leases. Mock tests run via `node --test runtime/tests/native-account-adapter.test.mjs` from the repository root. No production Provider support or release readiness is implied.

## Interactive native login (developer-only)

Create a native ChatGPT, Gemini or Doubao profile in Pack first. From an explicitly trusted source checkout with pinned Playwright Core and matching Chromium already installed, run `node runtime/browser-driver/native-login-cli.mjs <lowercase-profile-uuid>` from the repository root. Interactive TTY required. The tool resolves the profile from `~/.pack/agents-v2/agent-registry.json`, opens a visible browser at the fixed provider origin, then leaves login to the user. Enter closes the session and preserves browser data. Normal provider sign-in redirects are allowed; no password/captcha automation, account proof, cookie export, history extraction or model send. Storage: `native-sessions/<uuid>/browser-data`; shared execution lease: `execution-leases/<uuid>.lease` under the same private root. Unknown launch/close retains leases; never remove until independently confirmed safe. No Hub heartbeat or online claim. App provides the UUID and developer command only. DMG runtime packaging, lock/integrity, identity verification and live page drivers are still blocked.

## Native identity enrollment contract

`native-identity.mjs` is an insert-only local enrollment/verification module for a trusted host holding both account and browser leases. A real provider driver must observe stable account and workspace IDs, exact origin, model and memory settings; unknown settings/challenge/busy state fail closed. Explicit owner confirmation followed by a second fresh observation is required. Private per-profile HMAC record under `native-identities/` stores no raw account/workspace ID; it detects accidental account switches, not local compromise. A driver must use `verifyNativeIdentity` on every inspect and load the expected proof with `expectedNativeAccountProof`. No live provider reader exists yet; no identity enrollment action in the CLI or App is exposed. Native send authorization now receives the exact outgoing text and hash, and account state is checked before and after approval. Mock tests only.
