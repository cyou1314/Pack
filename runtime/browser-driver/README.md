# Pack Browser Driver (experimental)

Independent, opt-in adapter. Uses Playwright Core 1.65.1 (Apache-2.0) and its matching Chromium, with one persistent profile per Native Account UUID. Does not change the current Muse Engine, install browser dependencies on an end user's Mac, or connect to a real provider. Normal mode uses a visible browser; CI explicitly enables headless mode on a loopback fixture.

The caller must provision a dedicated private (0700), user-owned Pack root, NOT an existing everyday browser data directory. A directory lease rejects duplicate opens across cooperating managers. Ambiguous launch/close failures retain the lease for recovery: do not remove it until the owning process/browser has been confirmed stopped. No account login, credential export, network automation policy, cancellation of active page tasks, process-tree supervision, IPC or App integration yet. Raw context is an internal trusted-code interface, not an Agent tool surface.

The package version is fixed, but registry integrity metadata could not be retrieved here; no package-lock or browser artifact hash is fabricated. The separate experimental CI job installs the exact version and runs a local Chromium smoke test. Release packaging remains blocked until the dependency lock, verified downloads, licensing notices and full lifecycle acceptance are complete.

Run mock tests: node --test runtime/browser-driver/sessions.test.mjs.
Real Chromium smoke (separate dev environment): cd runtime/browser-driver; npm install --ignore-scripts --no-audit --no-fund; node node_modules/playwright-core/cli.js install chromium; node --test chromium.smoke.mjs.

## Native collaboration contract

`native-account-adapter.mjs` reuses BrowserSessions and requires a trusted Provider-specific page driver, independently enrolled account proof, verified model/memory state and explicit per-send authorization. It does not implement any live website selectors, login, challenge bypass or account-proof enrollment. A fresh conversation is required; one send only; correlated completed assistant reply only; unknown outcomes retain account execution leases. Mock tests run via `node --test runtime/tests/native-account-adapter.test.mjs` from the repository root. No production Provider support or release readiness is implied.
