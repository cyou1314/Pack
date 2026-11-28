# Muse4AI local engine boundary

Bridge now delegates local engine status, submit, poll, cancel and image
download to muse-engine-client.mjs. Endpoint allowlist is expressed as
fixed methods, local HTTP loopback origin only, no redirects, no arbitrary
request API, no client retries. Existing bridge state.json format, API key
file, Hub task semantics, account name, idempotency-key shape and generation
flow are preserved. The client checks identifier shape and submission key.
The bridge continues its existing retry with the same idempotency key; this
relies on the installed local Engine's implementation, not a guarantee
from Pack or an official remote API. No private protocol implementation.

Image artifact handling was subsequently hardened in
PACK-V2-MUSE-IMAGE-STORE.md; the original client extraction itself did
not redesign that behavior. No changes to Electron, login storage,
installed API, account data or production configuration. Copy the sibling
client together with bridge.mjs when deploying a loose bridge. The legacy
API is not the verified account collaboration adapter. Native login,
capability verification and per-egress enforcement remain outstanding.
Tests use fixture transport and exercise status, submit, poll, cancel,
failure propagation, and image integrity. Real Engine compatibility must
be checked on Mac. 
