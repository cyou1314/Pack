#!/bin/bash
# Read-only Pack/macOS inventory. Writes only a private report file; never emits credential values.
set -euo pipefail
[[ "$(uname -s)" == "Darwin" ]] || { echo "This inventory is for macOS only." >&2; exit 1; }
umask 077
STAMP="$(date -u '+%Y%m%d-%H%M%S')"
OUT="${1:-$HOME/commander-work/pack-mac-inventory-$STAMP.txt}"
[[ "$OUT" == /* ]] || OUT="$PWD/$OUT"
OUT_DIR="$(dirname "$OUT")"
mkdir -p "$OUT_DIR"
[[ ! -L "$OUT_DIR" ]] || { echo "Refusing symlinked report directory: $OUT_DIR" >&2; exit 1; }
[[ ! -e "$OUT" && ! -L "$OUT" ]] || { echo "Report already exists; choose another path: $OUT" >&2; exit 1; }
: > "$OUT"
chmod 600 "$OUT"

PACK_HOME="$HOME/.pack"
HUB="$PACK_HOME/local-hub"
ENGINE="$PACK_HOME/local-engine"
CONFIG="$PACK_HOME/config.json"
APP="/Applications/Pack.app"
AGENTS="$HOME/Library/LaunchAgents"
USER_ID="$(id -u)"

section() { printf '\n[%s]\n' "$1" >> "$OUT"; }
line() { printf '%s\n' "$1" >> "$OUT"; }

report_path() {
  local label="$1" path="$2" kind perms owner size
  if [[ -L "$path" ]]; then
    printf '%s=symlink\n' "$label" >> "$OUT"
  elif [[ -e "$path" ]]; then
    if [[ -d "$path" ]]; then kind="directory"; else kind="file"; fi
    perms="$(stat -f '%Sp' "$path" 2>/dev/null || printf 'unknown')"
    owner="$(stat -f '%Su:%Sg' "$path" 2>/dev/null || printf 'unknown')"
    size="$(du -sk "$path" 2>/dev/null | awk '{print $1 " KiB"}' || printf 'unknown')"
    printf '%s=%s; permissions=%s; owner=%s; size=%s\n' "$label" "$kind" "$perms" "$owner" "$size" >> "$OUT"
  else
    printf '%s=missing\n' "$label" >> "$OUT"
  fi
}

section "System"
line "macOS=$(sw_vers -productVersion 2>/dev/null || printf unknown)"
line "Build=$(sw_vers -buildVersion 2>/dev/null || printf unknown)"
line "Architecture=$(uname -m)"
line "InventoryUTC=$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

section "Pack paths (metadata only)"
report_path "PackHome" "$PACK_HOME"
report_path "Hub" "$HUB"
report_path "HubDatabase" "$HUB/pack.db"
report_path "HubAttachments" "$HUB/attachments"
report_path "DispatcherTokenFile" "$HUB/.dispatcher_token"
report_path "OwnerTokenFile" "$HUB/.owner_token"
report_path "HubBackups" "$HUB/backups"
report_path "Engine" "$ENGINE"
report_path "EngineData" "$ENGINE/data"
report_path "EngineLocalData" "$ENGINE/data/local"
report_path "Config" "$CONFIG"
report_path "PackApp" "$APP"

section "App bundle metadata"
INFO="$APP/Contents/Info.plist"
if [[ -f "$INFO" && ! -L "$INFO" ]]; then
  for key in CFBundleIdentifier CFBundleShortVersionString CFBundleVersion PackInstallerMarker CFBundleExecutable; do
    value="$(/usr/libexec/PlistBuddy -c "Print :$key" "$INFO" 2>/dev/null || true)"
    printf '%s=%s\n' "$key" "${value:-missing}" >> "$OUT"
  done
else
  line "InfoPlist=missing-or-untrusted"
fi

section "Configuration (key presence only; values are never emitted)"
if [[ -f "$CONFIG" && ! -L "$CONFIG" ]]; then
  for key in hubUrl token ownerToken; do
    if grep -Eq "\"${key}\"[[:space:]]*:" "$CONFIG"; then
      printf '%s=present\n' "$key" >> "$OUT"
    else
      printf '%s=missing\n' "$key" >> "$OUT"
    fi
  done
else
  line "ConfigKeys=unavailable"
fi

section "LaunchAgents (label, entry points, umask and loaded state only)"
for kind in hub watch backup engine; do
  label="com.pack.local-$kind"
  plist="$AGENTS/$label.plist"
  printf '[%s]\n' "$label" >> "$OUT"
  if [[ -f "$plist" && ! -L "$plist" ]]; then
    printf 'PlistLabel=%s\n' "$(/usr/libexec/PlistBuddy -c 'Print :Label' "$plist" 2>/dev/null || printf invalid)" >> "$OUT"
    program0="$(/usr/libexec/PlistBuddy -c 'Print :ProgramArguments:0' "$plist" 2>/dev/null || true)"
    program1="$(/usr/libexec/PlistBuddy -c 'Print :ProgramArguments:1' "$plist" 2>/dev/null || true)"
    printf 'Program0=%s\n' "$(basename "${program0:-unknown}")" >> "$OUT"
    printf 'Program1=%s\n' "$(basename "${program1:-unknown}")" >> "$OUT"
    printf 'Umask=%s\n' "$(/usr/libexec/PlistBuddy -c 'Print :Umask' "$plist" 2>/dev/null || printf missing)" >> "$OUT"
  else
    line "Plist=missing-or-symlink"
  fi
  state="$(launchctl print "gui/$USER_ID/$label" 2>/dev/null | awk '/state =/ {print $3; exit}' || true)"
  printf 'LoadedState=%s\n' "${state:-not-loaded}" >> "$OUT"
done

section "Local listeners (PID and process name only; no command arguments)"
for port in 8794 47821; do
  owners="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -Fp -Fc 2>/dev/null | awk '/^p/ {pid=substr($0,2)} /^c/ {cmd=substr($0,2); if (pid != "") {printf "%s:%s ", pid, cmd; pid=""}}' || true)"
  printf '127.0.0.1:%s=%s\n' "$port" "${owners:-no-listener-or-unavailable}" >> "$OUT"
done

section "Hub database health (read-only SQLite)"
if [[ -f "$HUB/pack.db" && ! -L "$HUB/pack.db" ]] && command -v sqlite3 >/dev/null 2>&1; then
  integrity="$(sqlite3 -readonly "$HUB/pack.db" 'PRAGMA quick_check;' 2>/dev/null || printf 'unavailable')"
  tables="$(sqlite3 -readonly "$HUB/pack.db" "SELECT COUNT(*) FROM sqlite_master WHERE type='table';" 2>/dev/null || printf 'unavailable')"
  line "SQLiteQuickCheck=$integrity"
  line "SQLiteTableCount=$tables"
else
  line "SQLiteQuickCheck=unavailable"
  line "SQLiteTableCount=unavailable"
fi

section "Data counts (no file contents)"
if [[ -d "$HUB/attachments" && ! -L "$HUB/attachments" ]]; then
  count="$(find "$HUB/attachments" -type f -print 2>/dev/null | wc -l | tr -d '[:space:]')"
  line "AttachmentFileCount=${count:-unavailable}"
fi
if [[ -d "$HUB/backups" && ! -L "$HUB/backups" ]]; then
  count="$(find "$HUB/backups" -maxdepth 1 -type f -name 'pack-*.db' -print 2>/dev/null | wc -l | tr -d '[:space:]')"
  latest="$(ls -1t "$HUB"/backups/pack-*.db 2>/dev/null | head -n 1 | sed 's|.*/||' || true)"
  line "DatabaseBackupCount=${count:-unavailable}"
  line "LatestDatabaseBackup=${latest:-none}"
  upgrades="$(find "$HUB/backups" -maxdepth 1 -type d -name 'upgrade-*' -print 2>/dev/null | wc -l | tr -d '[:space:]')"
  line "UpgradeBackupCount=${upgrades:-unavailable}"
fi

section "Privacy"
line "Keychain=not queried; no credential values were read"
line "Config/token contents=not emitted"
line "Pack services=not started, stopped, or modified"
line "SQLite opened with -readonly"
printf 'Read-only Pack inventory saved: %s\n' "$OUT"
