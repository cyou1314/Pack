#!/bin/bash
# 验证一套 Pack 备份：manifest、SQLite 完整性、附件归档可读性。
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HUB="${PACK_HUB_DIR:-$HERE}"
DIR="${PACK_BACKUP_DIR:-$HUB/backups}"
STAMP="${1:-}"

if [ -z "$STAMP" ]; then
  latest="$(ls -1t "$DIR"/pack-*.db 2>/dev/null | head -1 || true)"
  [ -n "$latest" ] || { echo "no database backup found in $DIR" >&2; exit 1; }
  STAMP="${latest##*/pack-}"
  STAMP="${STAMP%.db}"
fi

DB="$DIR/pack-$STAMP.db"
ATTACH="$DIR/attachments-$STAMP.tar.gz"
MANIFEST="$DIR/manifest-$STAMP.sha256"

for f in "$DB" "$ATTACH" "$MANIFEST"; do
  [ -f "$f" ] || { echo "missing backup component: $f" >&2; exit 1; }
done

(
  cd "$DIR"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c "$(basename "$MANIFEST")"
  else
    shasum -a 256 -c "$(basename "$MANIFEST")"
  fi
)

check="$(sqlite3 "$DB" "PRAGMA integrity_check;" 2>/dev/null | head -1)"
[ "$check" = "ok" ] || { echo "SQLite integrity failed: $check" >&2; exit 1; }

tar -tzf "$ATTACH" >/dev/null
printf 'BACKUP VERIFIED: %s\n' "$STAMP"
