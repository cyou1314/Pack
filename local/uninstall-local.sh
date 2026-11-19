#!/bin/bash
set -euo pipefail
HUB="$HOME/.pack/local-hub"
for label in com.pack.local-hub com.pack.local-watch com.pack.local-backup; do
  plist="$HOME/Library/LaunchAgents/$label.plist"
  if launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then launchctl bootout "gui/$(id -u)/$label"; fi
  if [ -f "$plist" ]; then rm "$plist"; fi
done
for name in hub.py room_watch.py backup-db.sh verify-backup.sh; do
  if [ -f "$HUB/$name" ]; then rm "$HUB/$name"; fi
done
# Remove only the service venv. Never remove the data directory or its contents.
/usr/bin/python3 - "$HUB/.venv" <<'PY'
import os, shutil, sys
path=sys.argv[1]
if os.path.islink(path): os.unlink(path)
elif os.path.isdir(path): shutil.rmtree(path)
PY
printf 'Agents and service files removed. Data preserved at %s (including pack.db, attachments and backups).\n' "$HUB"
