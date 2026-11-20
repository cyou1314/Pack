#!/usr/bin/env bash
# staging51 全套测试：重置全新 DB → 起隔离中枢（18798）→ 主套件 →
# 并发探针 → watcher 两道防线 → 迁移测试（自带双服务、自清理）→ 收尾。
set -u
cd "$(dirname "$0")"
PY=~/workspace/pack/hub/.venv/bin/python

kill_port() {
  local pid
  pid=$(ss -ltnp 2>/dev/null | grep ":$1 " | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
  if [ -n "${pid:-}" ]; then kill "$pid" 2>/dev/null; sleep 1; fi
}

kill_port 18798
rm -f pack.db pack.db-wal pack.db-shm .dispatcher_token .room_watch_state.json \
      test_fixtures.json
rm -rf attachments __pycache__ mig_old mig_new direct_env

PACK_HUB_PORT=18798 nohup $PY hub.py serve > server.log 2>&1 &
for i in $(seq 1 60); do ss -ltn 2>/dev/null | grep -q ':18798 ' && break; sleep 0.3; done

$PY test_staging51.py; RC1=$?
$PY test_concurrency.py; RC2=$?
$PY test_watcher.py; RC3=$?
$PY test_migration.py; RC4=$?
$PY test_direct.py; RC5=$?

kill_port 18798
echo "== main=$RC1 concurrency=$RC2 watcher=$RC3 migration=$RC4 direct=$RC5"
[ $RC1 -eq 0 ] && [ $RC2 -eq 0 ] && [ $RC3 -eq 0 ] && [ $RC4 -eq 0 ] && [ $RC5 -eq 0 ]
