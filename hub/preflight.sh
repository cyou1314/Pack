#!/bin/bash
# Pack VM / 本机部署预检。默认只检查静态与数据完整性；
# PACK_PREFLIGHT_SYSTEMD=1 时再检查 systemd 服务状态。
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
HUB="${PACK_HUB_DIR:-$HERE}"
DB="$HUB/pack.db"
fail=0

ok() { printf 'OK   %s\n' "$1"; }
warn() { printf 'WARN %s\n' "$1"; }
bad() { printf 'FAIL %s\n' "$1"; fail=1; }

for cmd in python3 sqlite3; do
  command -v "$cmd" >/dev/null 2>&1 && ok "command: $cmd" || bad "missing command: $cmd"
done

[ -f "$HUB/hub.py" ] && ok "hub.py present" || bad "hub.py missing"
[ -f "$HUB/room_watch.py" ] && ok "room_watch.py present" || bad "room_watch.py missing"
[ -x "$HUB/backup-db.sh" ] && ok "backup-db.sh executable" || bad "backup-db.sh missing/not executable"
[ -x "$HUB/verify-backup.sh" ] && ok "verify-backup.sh executable" || bad "verify-backup.sh missing/not executable"

if [ -f "$DB" ]; then
  integrity="$(sqlite3 "$DB" "PRAGMA integrity_check;" 2>/dev/null | head -1)"
  [ "$integrity" = "ok" ] && ok "SQLite integrity" || bad "SQLite integrity=$integrity"
else
  if [ "${PACK_PREFLIGHT_ALLOW_UNINITIALIZED:-0}" = "1" ]; then
    warn "pack.db not initialized yet"
  else
    bad "pack.db missing"
  fi
fi

if [ -f "$HUB/.dispatcher_token" ]; then
  perms="$(stat -c '%a' "$HUB/.dispatcher_token" 2>/dev/null || stat -f '%Lp' "$HUB/.dispatcher_token" 2>/dev/null || echo unknown)"
  case "$perms" in
    600) ok "dispatcher token permissions 0600" ;;
    *) bad "dispatcher token permissions=$perms (expected 0600)" ;;
  esac
else
  warn "dispatcher token file not present (Hub may create it during init)"
fi

mkdir -p "$HUB/attachments" "$HUB/backups"
ok "attachments directory present"
ok "backups directory present"

if ls "$HUB"/backups/pack-*.db >/dev/null 2>&1; then
  if "$HUB/verify-backup.sh" >/dev/null 2>&1; then
    ok "latest backup verifies"
  else
    bad "latest backup verification failed"
  fi
else
  warn "no backup exists yet"
fi

if [ "${PACK_PREFLIGHT_SYSTEMD:-0}" = "1" ]; then
  command -v systemctl >/dev/null 2>&1 || bad "systemctl not found"
  for unit in pack-hub.service pack-room-watch.service pack-db-backup.timer; do
    if systemctl is-enabled --quiet "$unit" 2>/dev/null && systemctl is-active --quiet "$unit" 2>/dev/null; then
      ok "systemd $unit enabled+active"
    else
      bad "systemd $unit is not enabled+active"
    fi
  done
fi

if [ "$fail" -ne 0 ]; then
  echo "PREFLIGHT: FAILED"
  exit 1
fi
echo "PREFLIGHT: OK"
