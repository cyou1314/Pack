#!/bin/bash
# Pack 在线备份：SQLite 快照 + content-addressed attachments + SHA-256 manifest。
# 默认保留最近 14 套备份；通过 PACK_HUB_DIR / PACK_BACKUP_DIR 可覆盖路径。
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HUB="${PACK_HUB_DIR:-$HERE}"
DIR="${PACK_BACKUP_DIR:-$HUB/backups}"
KEEP="${PACK_BACKUP_KEEP:-14}"
DB="$HUB/pack.db"
ATTACH="$HUB/attachments"
LOG="$HUB/backup.log"

mkdir -p "$DIR" "$ATTACH"
[ -f "$DB" ] || { echo "backup failed: missing $DB" >&2; exit 1; }
command -v sqlite3 >/dev/null 2>&1 || { echo "backup failed: sqlite3 not found" >&2; exit 1; }
command -v tar >/dev/null 2>&1 || { echo "backup failed: tar not found" >&2; exit 1; }

hash_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1"
  else
    shasum -a 256 "$1"
  fi
}

stamp="$(date +%Y%m%d-%H%M%S)"
db_out="$DIR/pack-$stamp.db"
attachments_out="$DIR/attachments-$stamp.tar.gz"
manifest_out="$DIR/manifest-$stamp.sha256"
db_tmp="$db_out.tmp"
attachments_tmp="$attachments_out.tmp"
manifest_tmp="$manifest_out.tmp"

cleanup() {
  rm -f "$db_tmp" "$attachments_tmp" "$manifest_tmp"
}
trap cleanup EXIT

# SQLite .backup creates a consistent online snapshot.
sqlite3 "$DB" ".backup '$db_tmp'"
db_check="$(sqlite3 "$db_tmp" "PRAGMA integrity_check;" 2>/dev/null | head -1)"
live_check="$(sqlite3 "$DB" "PRAGMA integrity_check;" 2>/dev/null | head -1)"
[ "$db_check" = "ok" ] || { echo "backup failed: snapshot integrity=$db_check" >&2; exit 1; }
[ "$live_check" = "ok" ] || { echo "backup failed: live integrity=$live_check" >&2; exit 1; }

# Attachments are content-addressed and written atomically by the Hub.
tar -C "$HUB" -czf "$attachments_tmp" "$(basename "$ATTACH")"

mv "$db_tmp" "$db_out"
mv "$attachments_tmp" "$attachments_out"
{
  hash_file "$db_out"
  hash_file "$attachments_out"
} > "$manifest_tmp"
mv "$manifest_tmp" "$manifest_out"

now="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
printf '%s backup=%s attachments=%s integrity=%s live=%s\n'   "$now" "$db_out" "$attachments_out" "$db_check" "$live_check" >> "$LOG"

# Rotate complete backup sets, not individual files.
ls -1t "$DIR"/pack-*.db 2>/dev/null | tail -n +"$((KEEP + 1))" | while IFS= read -r old_db; do
  old_stamp="${old_db##*/pack-}"
  old_stamp="${old_stamp%.db}"
  rm -f "$old_db" "$DIR/attachments-$old_stamp.tar.gz" "$DIR/manifest-$old_stamp.sha256"
done

echo "BACKUP OK: $stamp"
