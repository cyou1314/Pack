#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYROOT="${PACK_BUILD_PYTHON_ROOT:-$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python}"
VENV="${PACK_BUILD_VENV:-$HOME/.pack/local-hub/.venv}"
ENGINE="${PACK_BUILD_ENGINE:-$HOME/pack-headless-l2}"
OUT="${1:-$HOME/commander-work/Pack-local-installer.dmg}"
[[ ! -e "$OUT" && ! -L "$OUT" ]] || { echo 'Output exists; choose a new path.' >&2; exit 1; }
"$PYROOT/bin/python3" -c 'import sys; assert sys.version_info[:2] == (3,12)'
"$VENV/bin/python" -m pip freeze | sort | diff -u "$ROOT/installer/python-requirements.lock" -
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/pack-installer-stage.XXXXXX")"
mkdir -p "$STAGE/python/bin" "$STAGE/python/lib/python3.12/site-packages" "$STAGE/hub" "$STAGE/engine/app"
bash "$ROOT/app/make-bundle.sh" "$STAGE/Pack.app"
rsync -a --exclude='site-packages' --exclude='__pycache__' "$PYROOT/lib/python3.12/" "$STAGE/python/lib/python3.12/"
rsync -a "$PYROOT/bin/python3.12" "$STAGE/python/bin/"
ln -s python3.12 "$STAGE/python/bin/python3"
rsync -a --exclude='pip' --exclude='pip-*' --exclude='__pycache__' "$VENV/lib/python3.12/site-packages/" "$STAGE/python/lib/python3.12/site-packages/"
for f in hub.py room_watch.py backup-db.sh verify-backup.sh; do cp "$ROOT/hub/$f" "$STAGE/hub/$f"; done
cp -R "$ROOT/hub/core" "$STAGE/hub/"
cp -R "$ROOT/hub/services" "$STAGE/hub/"
for f in headless.cjs gateway-page.js package.json; do cp "$ENGINE/app/$f" "$STAGE/engine/app/$f"; done
ditto "$ENGINE/Electron.app" "$STAGE/engine/Electron.app"
/usr/libexec/PlistBuddy -c 'Add :PackInstallerMarker string com.cyou.pack.installer.v1' "$STAGE/Pack.app/Contents/Info.plist"
codesign --force --sign - "$STAGE/Pack.app" >/dev/null
codesign --verify --deep --strict "$STAGE/Pack.app"
plutil -lint "$STAGE/Pack.app/Contents/Info.plist" >/dev/null
for f in manager.py install.command uninstall.command README.txt python-requirements.lock; do cp "$ROOT/installer/$f" "$STAGE/$f"; done
ln -s /Applications "$STAGE/Applications"
# The hub and engine payload must contain no user databases, tokens or sessions.
if find "$STAGE/hub" "$STAGE/engine" \( -name 'pack.db*' -o -name 'muse.sqlite*' -o -name 'Cookies' -o -name 'vault.json' -o -name '.dispatcher_token' -o -name '.owner_token' -o -name 'tokens.json' -o -name '*.token' -o -name '*.sqlite*' -o -name 'Partitions' -o -name 'session.json' -o -name 'login.json' \) -print | grep -q .; then echo 'Unexpected user data in payload.' >&2; exit 1; fi
env -i HOME="$STAGE" PATH=/nonexistent PYTHONNOUSERSITE=1 "$STAGE/python/bin/python3" -c 'import asyncio,inspect,sys; sys.path.insert(0,sys.argv[1]); import fastmcp,sqlite3,ssl,hub; assert fastmcp.__version__ == "4.1.8"; tools=hub.build_server().list_tools(); tools=asyncio.run(tools) if inspect.isawaitable(tools) else tools; names={tool.name for tool in tools}; assert {"room_list","member_list"}.issubset(names), names' "$STAGE/hub"
APP_VERSION=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$STAGE/Pack.app/Contents/Info.plist")
APP_BUILD=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$STAGE/Pack.app/Contents/Info.plist")
MIN_MACOS=$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "$STAGE/Pack.app/Contents/Info.plist")
PYTHON_VERSION=$("$STAGE/python/bin/python3" -c 'import platform; print(platform.python_version())')
FASTMCP_VERSION=$("$STAGE/python/bin/python3" -c 'import fastmcp; print(fastmcp.__version__)')
ENGINE_VERSION=$("$STAGE/python/bin/python3" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("version", "unknown"))' "$STAGE/engine/app/package.json")
SOURCE_REV=$(git -C "$ROOT" rev-parse --verify HEAD 2>/dev/null || printf 'unknown')
cat > "$STAGE/BUILD-MANIFEST.txt" <<EOF
PackVersion=$APP_VERSION
PackBuild=$APP_BUILD
MinimumMacOS=$MIN_MACOS
PythonVersion=$PYTHON_VERSION
FastMCPVersion=$FASTMCP_VERSION
EngineVersion=$ENGINE_VERSION
SourceRevision=$SOURCE_REV
EOF
mkdir -p "$(dirname "$OUT")"
hdiutil create -srcfolder "$STAGE" -volname 'Pack 本地版' -format UDZO "$OUT"
hdiutil verify "$OUT"
shasum -a 256 "$OUT"
printf 'Built %s\nStaging directory: %s\n' "$OUT" "$STAGE"
