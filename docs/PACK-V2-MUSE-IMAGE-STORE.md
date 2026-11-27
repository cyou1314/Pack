# Muse image artifact storage

The local engine client now uses a dedicated image store. Normal legacy
task-<id>-<asset_id>.<extension> names are retained. Noncanonical or long
asset identifiers receive a deterministic hashed basename rather than
lossy sanitization. Existing files are never renamed, overwritten or
automatically deleted. Private owner-only image directory required; a
symlink or nonregular/unowned/insecure existing file fails closed.

Metadata validates MIME allowlist, optional size and SHA-256. Downloads
are streamed with a 100 MiB cap, checked against available metadata,
synced to a unique exclusive temporary file, and published atomically
with a hard link (no replacement). Verified prior file with a supplied
digest can be reused without a network request. When the Engine does not
supply a digest, re-download and compare actual bytes; size alone never
proves identity. Concurrent publication of identical data is idempotent.
Conflict is a manual-review condition rather than an overwrite. Invalid
metadata, unsafe path, conflicting file and integrity failure are persisted
as blocked by the existing Bridge state machine. Transient transport
failures retain its existing recovery behavior.

Cleanup is limited to this invocation's own temporary file. Crash leftovers
and historical .part files are not deleted. No directory fsync guarantee,
malicious same-UID filesystem race protection, file format decoding,
video storage, full asset catalog or real Engine acceptance in this slice.
The file hash verifies integrity, not actual image validity or Provider
identity. No new dependency or config key. Install the sibling image
store along with the client and Bridge. Tests cover duplicate, conflict,
missing digest, partial cleanup, unsafe path/metadata and concurrent
publication using temporary files and fixture transport.
