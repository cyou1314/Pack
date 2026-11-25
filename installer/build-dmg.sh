#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYROOT="${PACK_BUILD_PYTHON_ROOT:-$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python}"
VENV="${PACK_BUILD_VENV:-$HOME/.pack/local-hub/.venv}"
ENGINE="${PACK_BUILD_ENGINE:-$HOME/pack-headless-l2}"
OUT="${1:-$HOME/commander-work/Pack-local-installer.dmg}"
ENGINE_LOCK="${PACK_BUILD_ENGINE_LOCK:-$ROOT/installer/engine.lock.json}"
[ ! -e "$OUT" ] && [ ! -L "$OUT" ] || { echo 'Output exists; choose a new path.' >&2; exit 1; }

fail() { echo "Pack DMG preflight failed: $*" >&2; exit 1; }
# Explicit program-only allowlist; parity with manager.py is checked in CI.
HUB_PROGRAM_FILES=(hub.py room_watch.py backup-db.sh verify-backup.sh collaboration_policy.py collaboration_store.py collaboration_dispatch.py collaboration_guard.py)
[ -f "$ENGINE_LOCK" ] && [ ! -L "$ENGINE_LOCK" ] || fail "missing approved Engine lock: $ENGINE_LOCK (see installer/BUILDING.md)"
for f in "${HUB_PROGRAM_FILES[@]}"; do
  [ -f "$ROOT/hub/$f" ] && [ ! -L "$ROOT/hub/$f" ] || fail "missing or unsafe Hub program file: $f"
done
for cmd in rsync ditto codesign hdiutil swift git; do
  command -v "$cmd" >/dev/null 2>&1 || fail "missing macOS build tool: $cmd"
done
[ -x "$PYROOT/bin/python3" ] || fail "missing embedded Python 3.12 runtime: $PYROOT/bin/python3 (set PACK_BUILD_PYTHON_ROOT)"
[ -d "$PYROOT/lib/python3.12" ] || fail "missing Python 3.12 standard library: $PYROOT/lib/python3.12"
[ -x "$VENV/bin/python" ] || fail "missing Hub build venv: $VENV/bin/python (set PACK_BUILD_VENV)"
[ -d "$VENV/lib/python3.12/site-packages" ] || fail "missing Hub venv site-packages: $VENV/lib/python3.12/site-packages"
"$PYROOT/bin/python3" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)' || fail "embedded Python runtime must be 3.12"
"$VENV/bin/python" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)' || fail "Hub build venv must use Python 3.12"
[ -f "$ROOT/installer/python-requirements.lock" ] || fail "missing Python dependency lock"
[ -f "$ENGINE/app/headless.cjs" ] || fail "missing external Engine source: $ENGINE/app/headless.cjs (set PACK_BUILD_ENGINE)"
[ -f "$ENGINE/app/gateway-page.js" ] || fail "missing external Engine source: $ENGINE/app/gateway-page.js (set PACK_BUILD_ENGINE)"
[ -f "$ENGINE/app/package.json" ] || fail "missing external Engine package manifest: $ENGINE/app/package.json"
[ -x "$ENGINE/Electron.app/Contents/MacOS/Electron" ] || fail "missing executable Electron bundle: $ENGINE/Electron.app"
for contract in 'PACK_L2_SOCKET_DIR' 'handoff.sock' 'com.cyou.pack.l2-handoff'; do
  grep -Fq "$contract" "$ENGINE/app/headless.cjs" || fail "Engine bundle is missing L2 handoff contract string: $contract"
done
"$PYROOT/bin/python3" "$ROOT/runtime/l2-handoff/verify-electron.py" "$ENGINE/Electron.app" || fail "Electron cookie encryption fuse is not enabled; prepare a separate encrypted bundle first"
codesign --verify --deep --strict "$ENGINE/Electron.app" || fail "Electron bundle signature verification failed"
[ -f /usr/libexec/PlistBuddy ] || fail "missing /usr/libexec/PlistBuddy"
"$PYROOT/bin/python3" -c 'import sys; assert sys.version_info[:2] == (3,12)'
"$VENV/bin/python" -m pip freeze | sort | diff -u "$ROOT/installer/python-requirements.lock" -
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/pack-installer-stage.XXXXXX")"
PACK_COMMIT="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
cp "$ENGINE_LOCK" "$STAGE/engine.lock.json"
ENGINE_COMMIT="$("$PYROOT/bin/python3" "$ROOT/installer/engine-lock.py" verify --engine "$ENGINE" --lock "$STAGE/engine.lock.json")"
ENGINE_VERSION="$("$PYROOT/bin/python3" -c 'import json,sys; print(json.load(open(sys.argv[1]))["engine_package_version"])' "$STAGE/engine.lock.json")"
cat > "$STAGE/BUILD-INFO.txt" <<EOF
Pack commit: $PACK_COMMIT
Engine source revision (declared in lock): $ENGINE_COMMIT
Engine payload: SHA-256 lock verified
Engine package version: $ENGINE_VERSION
L2 handoff contract: static marker check
Electron cookie encryption: enabled
Python runtime: 3.12
Built UTC: $(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
# Build the .app directly into the disposable staging directory. A clean
# clone has no app/Pack.app yet, and we must not delete a developer's bundle.
bash "$ROOT/app/make-bundle.sh" "$STAGE/Pack.app"
mkdir -p "$STAGE/python/bin" "$STAGE/python/lib/python3.12/site-packages" "$STAGE/hub" "$STAGE/engine/app"
rsync -a --exclude='site-packages' --exclude='__pycache__' "$PYROOT/lib/python3.12/" "$STAGE/python/lib/python3.12/"
rsync -a "$PYROOT/bin/python3.12" "$STAGE/python/bin/"
ln -s python3.12 "$STAGE/python/bin/python3"
rsync -a --exclude='pip' --exclude='pip-*' --exclude='__pycache__' "$VENV/lib/python3.12/site-packages/" "$STAGE/python/lib/python3.12/site-packages/"
for f in "${HUB_PROGRAM_FILES[@]}"; do cp "$ROOT/hub/$f" "$STAGE/hub/$f"; done
for f in headless.cjs gateway-page.js package.json; do cp "$ENGINE/app/$f" "$STAGE/engine/app/$f"; done
ditto "$ENGINE/Electron.app" "$STAGE/engine/Electron.app"
"$PYROOT/bin/python3" "$ROOT/installer/engine-lock.py" verify --engine "$STAGE/engine" --lock "$STAGE/engine.lock.json" >/dev/null
for contract in 'PACK_L2_SOCKET_DIR' 'handoff.sock' 'com.cyou.pack.l2-handoff'; do
  grep -Fq "$contract" "$STAGE/engine/app/headless.cjs" || fail "staged Engine is missing L2 handoff contract string: $contract"
done
"$PYROOT/bin/python3" "$ROOT/runtime/l2-handoff/verify-electron.py" "$STAGE/engine/Electron.app"
codesign --verify --deep --strict "$STAGE/engine/Electron.app"
/usr/libexec/PlistBuddy -c 'Add :PackInstallerMarker string com.cyou.pack.installer.v1' "$STAGE/Pack.app/Contents/Info.plist"
codesign --force --sign - "$STAGE/Pack.app" >/dev/null
codesign --verify --deep --strict "$STAGE/Pack.app"
for f in manager.py install.command uninstall.command README.txt python-requirements.lock; do cp "$ROOT/installer/$f" "$STAGE/$f"; done
ln -s /Applications "$STAGE/Applications"
# The hub and engine payload must contain no user databases, tokens or sessions.
if find "$STAGE/hub" "$STAGE/engine" \( -name 'pack.db*' -o -name 'Cookies' -o -name 'vault.json' -o -name '.dispatcher_token' -o -name '*.sqlite*' -o -name 'Partitions' \) -print | grep -q .; then echo 'Unexpected user data in payload.' >&2; exit 1; fi
env -i HOME="$STAGE" PATH=/nonexistent PYTHONNOUSERSITE=1 "$STAGE/python/bin/python3" -c 'import fastmcp,sqlite3,ssl; assert fastmcp.__version__ == "4.1.8"'
# Validate imports from the staged payload, not the source checkout. Do not
# initialize a database, start a service or create bytecode in the bundle.
env -i HOME="$STAGE" PATH=/nonexistent PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$STAGE/hub" "$STAGE/python/bin/python3" -B -c 'import hub, collaboration_policy, collaboration_store, collaboration_dispatch, collaboration_guard'
mkdir -p "$(dirname "$OUT")"
hdiutil create -srcfolder "$STAGE" -volname 'Pack 本地版' -format UDZO "$OUT"
printf 'Built %s\nStaging directory: %s\n' "$OUT" "$STAGE"
