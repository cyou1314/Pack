#!/bin/bash
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HUB="$HOME/.pack/local-hub"
AGENTS="$HOME/Library/LaunchAgents"
PYTHON="${PACK_PYTHON:-python3}"
"$PYTHON" -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ is required"'
mkdir -p "$HUB" "$AGENTS"
chmod 700 "$HUB"
if [ ! -x "$HUB/.venv/bin/python" ]; then "$PYTHON" -m venv "$HUB/.venv"; fi
"$HUB/.venv/bin/python" -m pip install 'fastmcp==4.1.8'
for f in hub.py room_watch.py backup-db.sh verify-backup.sh; do cp "$ROOT/hub/$f" "$HUB/$f"; done
if [ -f "$HUB/pack.db" ] && [ ! -s "$HUB/.dispatcher_token" ]; then echo 'Existing database has no dispatcher token; refusing to initialize.' >&2; exit 1; fi
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
