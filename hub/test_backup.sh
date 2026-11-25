#!/bin/bash
# backup-db.sh 的离线回归：不碰真实 Pack 数据，只验证备份/校验/轮换。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/pack-backup-test.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

mkdir -p "$TMP_ROOT/attachments" "$TMP_ROOT/backups"
sqlite3 "$TMP_ROOT/pack.db" 'CREATE TABLE smoke(id INTEGER PRIMARY KEY, value TEXT); INSERT INTO smoke(value) VALUES ("ok");'
printf 'attachment smoke test\n' > "$TMP_ROOT/attachments/sha256-smoke.txt"

PACK_HUB_DIR="$TMP_ROOT" PACK_BACKUP_DIR="$TMP_ROOT/backups" "$SCRIPT_DIR/backup-db.sh"
PACK_HUB_DIR="$TMP_ROOT" PACK_BACKUP_DIR="$TMP_ROOT/backups" "$SCRIPT_DIR/verify-backup.sh"

latest="$(ls -1t "$TMP_ROOT"/backups/pack-*.db | head -1)"
stamp="${latest##*/pack-}"
stamp="${stamp%.db}"
archive="$TMP_ROOT/backups/attachments-$stamp.tar.gz"
manifest="$TMP_ROOT/backups/manifest-$stamp.sha256"

[ -f "$archive" ]
[ -f "$manifest" ]
sqlite3 "$latest" 'SELECT value FROM smoke;' | grep -qx 'ok'
tar -tzf "$archive" | grep -q 'attachments/sha256-smoke.txt'
if grep -Fq "$TMP_ROOT" "$manifest"; then
  echo "manifest must use relative paths" >&2
  exit 1
fi

# A copied backup set must verify from a different directory.
mkdir -p "$TMP_ROOT/relocated"
cp "$latest" "$archive" "$manifest" "$TMP_ROOT/relocated/"
PACK_HUB_DIR="$TMP_ROOT" PACK_BACKUP_DIR="$TMP_ROOT/relocated" \
  "$SCRIPT_DIR/verify-backup.sh" "$stamp"

# An orphan snapshot must not shadow the newest complete backup.
orphan="$TMP_ROOT/backups/pack-20990101-000000-999999.db"
printf 'incomplete' > "$orphan"
PACK_HUB_DIR="$TMP_ROOT" PACK_BACKUP_DIR="$TMP_ROOT/backups" \
  "$SCRIPT_DIR/verify-backup.sh" >/dev/null

# Rotation is a complete-set operation.
for n in 1 2 3; do
  sleep 1
  PACK_HUB_DIR="$TMP_ROOT" PACK_BACKUP_DIR="$TMP_ROOT/backups" PACK_BACKUP_KEEP=2 "$SCRIPT_DIR/backup-db.sh" >/dev/null
done
[ ! -f "$orphan" ]
count="$(ls -1 "$TMP_ROOT"/backups/pack-*.db | wc -l | tr -d ' ')"
[ "$count" -le 2 ]

echo "BACKUP TEST: PASS"
