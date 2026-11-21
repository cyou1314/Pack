#!/usr/bin/env bash
# staging53 全套测试：新套件（question 全链）→ 既有回归链
# （staging51/fixes51/并发/watcher/staging52/脚本，与 staging52 同序）
# → 进程内直测（_question_state 本体 + staging51 配额直测）→ 迁移测试。
# 每段独立全新 DB；隔离端口 18798（迁移自带 18795/18796）。
set -u
cd "$(dirname "$0")"
PY=~/workspace/pack/hub/.venv/bin/python

kill_port() {
  local pid
  pid=$(ss -ltnp 2>/dev/null | grep ":$1 " | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
  if [ -n "${pid:-}" ]; then kill "$pid" 2>/dev/null; sleep 1; fi
}

fresh_db() {
  kill_port 18798
  rm -f pack.db pack.db-wal pack.db-shm .dispatcher_token .room_watch_state.json \
        test_fixtures.json
  rm -rf attachments __pycache__
}

start_hub() {
  PACK_HUB_PORT=18798 nohup $PY hub.py serve > "server-$1.log" 2>&1 &
  for i in $(seq 1 60); do ss -ltn 2>/dev/null | grep -q ':18798 ' && break; sleep 0.3; done
}

# ---- 第一段：staging53 新套件 ----
fresh_db
start_hub 53a
$PY test_staging53.py; RC53=$?
kill_port 18798
$PY test_question_state.py; RCQS=$?

# ---- 第二段：既有回归链（全新 DB） ----
fresh_db
start_hub 53b
$PY test_staging51.py; RC1=$?
$PY test_fixes51.py; RC6=$?
$PY test_concurrency.py; RC2=$?
$PY test_watcher.py; RC3=$?
$PY test_staging52.py; RC7=$?
$PY test_agent_script.py; RC8=$?
kill_port 18798

# ---- 第三段：直测 + 迁移 ----
$PY test_direct.py; RC5=$?
$PY test_migration53.py; RC4=$?

# ---- 第四段：负载探针（独立全新 DB） ----
fresh_db
start_hub 53c
$PY test_load53.py; RC9=$?
kill_port 18798

echo "== staging53=$RC53 qstate=$RCQS | main51=$RC1 fixes=$RC6 concurrency=$RC2 watcher=$RC3 inbox52=$RC7 script=$RC8 | direct=$RC5 migration=$RC4 load=$RC9"
[ $RC53 -eq 0 ] && [ $RCQS -eq 0 ] && [ $RC1 -eq 0 ] && [ $RC6 -eq 0 ] && \
[ $RC2 -eq 0 ] && [ $RC3 -eq 0 ] && [ $RC7 -eq 0 ] && [ $RC8 -eq 0 ] && \
[ $RC5 -eq 0 ] && [ $RC4 -eq 0 ] && [ $RC9 -eq 0 ]
