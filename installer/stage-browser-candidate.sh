#!/bin/bash
# Candidate payload only. No install, no use of a user's browser profile.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
[ "$#" -eq 3 ] || { echo "usage: stage-browser-candidate.sh NEW_DIRECTORY BROWSER_CACHE NODE_BINARY" >&2; exit 2; }
OUT="$1"
CACHE="$2"
NODE="$3"
[ -d "$(dirname "$OUT")" ] && [ ! -e "$OUT" ] && [ ! -L "$OUT" ] || exit 1
[ -d "$CACHE" ] && [ ! -L "$CACHE" ] && [ -x "$NODE" ] || exit 1
# Reserve output atomically. A partial stage is left for inspection on failure.
mkdir -m 700 "$OUT"
mkdir -p "$OUT/node/bin" "$OUT/runtime/browser-driver/node_modules" "$OUT/browsers"
cp -L "$NODE" "$OUT/node/bin/node"
for f in account-execution-leases.mjs collaboration-runner.mjs; do
  cp "$ROOT/runtime/$f" "$OUT/runtime/$f"
done
mkdir "$OUT/runtime/agent-core"
cp "$ROOT/runtime/agent-core/collaboration-execution-supervisor.mjs" "$OUT/runtime/agent-core/collaboration-execution-supervisor.mjs"
for f in package.json package-lock.json sessions.mjs playwright.mjs native-login.mjs native-login-cli.mjs native-diagnostics.mjs native-identity.mjs native-account-adapter.mjs chatgpt-page-driver.mjs chatgpt-collaboration-host.mjs; do
  cp "$ROOT/runtime/browser-driver/$f" "$OUT/runtime/browser-driver/$f"
done
ditto "$ROOT/runtime/browser-driver/node_modules/playwright-core" "$OUT/runtime/browser-driver/node_modules/playwright-core"
for dir in chromium-1255 chromium_headless_shell-1255 ffmpeg-1013; do
  [ -d "$CACHE/$dir" ] && [ ! -L "$CACHE/$dir" ] || exit 1
  ditto "$CACHE/$dir" "$OUT/browsers/$dir"
done
