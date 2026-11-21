#!/bin/bash
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PACK_HOME="$HOME/.pack"
HUB="$PACK_HOME/local-hub"
LIBRARY="$HOME/Library"
AGENTS="$LIBRARY/LaunchAgents"
PYTHON="${PACK_PYTHON:-python3}"
fail() { echo "ERROR: $*" >&2; exit 1; }
"$PYTHON" -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ is required"'

# Reject symlinked roots and existing installs before writing files or plists.
for path in "$PACK_HOME" "$LIBRARY" "$AGENTS"; do
  [[ ! -L "$path" ]] || fail "refusing symlinked directory: $path"
  [[ ! -e "$path" || -d "$path" ]] || fail "expected a directory: $path"
done
[[ ! -L "$HUB" && ! -e "$HUB" ]] || fail "existing local Hub path; refusing to overwrite: $HUB"
USER_ID="$(id -u)"
LABELS=(com.pack.local-hub com.pack.local-watch com.pack.local-backup)
for label in "${LABELS[@]}"; do
  plist="$AGENTS/$label.plist"
  [[ ! -L "$plist" && ! -e "$plist" ]] || fail "existing LaunchAgent plist; refusing to overwrite: $plist"
  if launchctl print "gui/$USER_ID/$label" >/dev/null 2>&1; then
    fail "LaunchAgent label is already loaded: $label"
  fi
done

mkdir -p "$PACK_HOME" "$HUB" "$AGENTS"
chmod 700 "$PACK_HOME" "$HUB"
if [ ! -x "$HUB/.venv/bin/python" ]; then "$PYTHON" -m venv "$HUB/.venv"; fi
"$HUB/.venv/bin/python" -m pip install 'fastmcp==4.1.8'
for f in hub.py room_watch.py backup-db.sh verify-backup.sh; do cp "$ROOT/hub/$f" "$HUB/$f"; done
cp -R "$ROOT/hub/core" "$HUB/"
cp -R "$ROOT/hub/services" "$HUB/"
(
  cd "$HUB"
  "$HUB/.venv/bin/python" - <<'PY'
import asyncio, inspect, hub
tools = hub.build_server().list_tools()
if inspect.isawaitable(tools):
    tools = asyncio.run(tools)
names = {tool.name for tool in tools}
assert {"room_list", "member_list"}.issubset(names), names
PY
)
"$HUB/.venv/bin/python" "$HUB/hub.py" init
chmod 600 "$HUB/.dispatcher_token"
mkdir -p "$HUB/attachments"
"$HUB/.venv/bin/python" - "$ROOT/local" "$HUB" "$AGENTS" <<'PY'
import pathlib,sys,xml.sax.saxutils
src,hub,dst=map(pathlib.Path,sys.argv[1:]); esc=xml.sax.saxutils.escape
for name in ('com.pack.local-hub','com.pack.local-watch','com.pack.local-backup'):
    text=(src/(name+'.plist')).read_text().replace('__HUB__',esc(str(hub))).replace('__PYTHON__',esc(str(hub/'.venv/bin/python')))
    (dst/(name+'.plist')).write_text(text)
PY
for label in com.pack.local-hub com.pack.local-watch com.pack.local-backup; do
  plutil -lint "$AGENTS/$label.plist" >/dev/null
  if ! launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then launchctl bootstrap "gui/$(id -u)" "$AGENTS/$label.plist"; fi
done
echo "Local hub installed at $HUB; listening on 127.0.0.1:8794."
