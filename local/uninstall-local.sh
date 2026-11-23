#!/bin/bash
set -euo pipefail
PACK_HOME="$HOME/.pack"
HUB="$PACK_HOME/local-hub"
LIBRARY="$HOME/Library"
AGENTS="$LIBRARY/LaunchAgents"
USER_ID="$(id -u)"
LABELS=(com.pack.local-hub com.pack.local-watch com.pack.local-backup)
PROGRAMS=(hub.py room_watch.py backup-db.sh)

fail() { echo "ERROR: $*" >&2; exit 1; }
is_loaded() { launchctl print "gui/$USER_ID/$1" >/dev/null 2>&1; }

# Reject symlinked roots before resolving any service or data path.
for path in "$PACK_HOME" "$HUB" "$LIBRARY" "$AGENTS"; do
  [[ ! -L "$path" ]] || fail "refusing symlinked path: $path"
  [[ ! -e "$path" || -d "$path" ]] || fail "expected a directory: $path"
done

# Validate every plist identity before stopping or deleting any service.
for index in "${!LABELS[@]}"; do
  label="${LABELS[$index]}"
  expected_program="$HUB/${PROGRAMS[$index]}"
  plist="$AGENTS/$label.plist"
  if [[ -L "$plist" ]]; then
    fail "refusing symlinked LaunchAgent plist: $plist"
  elif [[ -e "$plist" && ! -f "$plist" ]]; then
    fail "LaunchAgent plist is not a regular file: $plist"
  elif [[ -f "$plist" ]]; then
    actual_label="$(plutil -extract Label raw -o - "$plist" 2>/dev/null)" || fail "cannot read LaunchAgent label: $plist"
    [[ "$actual_label" == "$label" ]] || fail "LaunchAgent label mismatch: $plist"
    if [[ "$label" == "com.pack.local-backup" ]]; then
      expected_launcher="/bin/bash"
    else
      expected_launcher="$HUB/.venv/bin/python"
    fi
    actual_launcher="$(/usr/libexec/PlistBuddy -c 'Print :ProgramArguments:0' "$plist" 2>/dev/null)" || fail "cannot read LaunchAgent launcher: $plist"
    [[ "$actual_launcher" == "$expected_launcher" ]] || fail "LaunchAgent launcher mismatch: $plist"
    actual_program="$(/usr/libexec/PlistBuddy -c 'Print :ProgramArguments:1' "$plist" 2>/dev/null)" || fail "cannot read LaunchAgent program path: $plist"
    [[ "$actual_program" == "$expected_program" ]] || fail "LaunchAgent program path mismatch: $plist"
  elif is_loaded "$label"; then
    fail "loaded LaunchAgent has no verifiable plist; refusing to stop it: $label"
  fi
done

for label in "${LABELS[@]}"; do
  plist="$AGENTS/$label.plist"
  if is_loaded "$label"; then launchctl bootout "gui/$USER_ID/$label"; fi
  if [[ -f "$plist" ]]; then rm "$plist"; fi
done

for name in hub.py room_watch.py backup-db.sh verify-backup.sh; do
  path="$HUB/$name"
  if [[ -L "$path" || -f "$path" ]]; then rm "$path"; fi
done
for name in core services; do
  path="$HUB/$name"
  if [[ -L "$path" ]]; then rm "$path"
  elif [[ -d "$path" ]]; then rm -rf "$path"
  fi
done
# Remove only the service venv. Never remove the data directory or its contents.
/usr/bin/python3 - "$HUB/.venv" <<'PY'
import os, shutil, sys
path=sys.argv[1]
if os.path.islink(path): os.unlink(path)
elif os.path.isdir(path): shutil.rmtree(path)
PY
printf 'Agents and service files removed. Data preserved at %s (including pack.db, attachments and backups).\n' "$HUB"
