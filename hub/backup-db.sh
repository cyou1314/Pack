#!/bin/bash
# pack.db 每日备份 + 完整性自检：在线 .backup（WAL 安全），留最近 14 份
set -u
HUB=/home/hatch/workspace/pack/hub
DIR=$HUB/backups
mkdir -p "$DIR"
TS=$(date +%Y%m%d-%H%M)
OUT=$DIR/pack-$TS.db
LOG=$HUB/backup.log
sqlite3 "$HUB/pack.db" ".backup '$OUT'" 2>>"$LOG" || { echo "$(date -Is) BACKUP-FAIL" >>"$LOG"; exit 1; }
CHK=$(sqlite3 "$OUT" "PRAGMA integrity_check;" 2>/dev/null | head -1)
LIVE=$(sqlite3 "$HUB/pack.db" "PRAGMA integrity_check;" 2>/dev/null | head -1)
echo "$(date -Is) backup=$OUT integrity=$CHK live=$LIVE" >>"$LOG"
ls -t "$DIR"/pack-*.db | tail -n +15 | xargs -r rm -f
[ "$CHK" = "ok" ] && [ "$LIVE" = "ok" ]
