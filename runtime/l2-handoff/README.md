# L2 local handoff

`build-headless.mjs` bundles the existing Muse4AI `headless.ts` with `engine.cjs` without editing that source tree. `prepare-electron.py` creates a **new** Electron bundle with the cookie encryption fuse enabled; it never patches the source bundle. Example:

```sh
python3 runtime/l2-handoff/prepare-electron.py /path/to/original/Electron.app /path/to/new/Electron.app
node runtime/l2-handoff/build-headless.mjs /path/to/muse4ai/desktop /path/to/headless-output
```

Copy the existing headless `package.json` to the new output. Start the new Electron with that output as its app path and the normal `PACK_ENGINE_ROAMING`, `PACK_ENGINE_LOCAL`, and `PACK_ENGINE_PORT` settings. The helper opens `~/.pack/l2-ipc/handoff.sock` inside a user-only directory. The socket carries a member name and random Keychain ticket only, never cookie contents. The app writes the cookie payload to the ticket; the engine reads and removes it, sets cookies using Electron's session API, verifies encrypted storage, and runs the normal session check. Failures are returned without printing cookie data.

Enabling Electron's cookie encryption is a one-way migration: disabling it against the migrated store may make that store unreadable. Before changing a production process, stop it and back up the entire data directory **and** the old runtime/build. Rollback requires restoring the matching old runtime and the complete pre-migration data directory. Do not point a fuse-disabled runtime at a migrated store. The bridge remains unchanged. The application asks for an explicit user action; it never exports a session automatically.
