#!/usr/bin/env bash
# staging54 全套测试：新套件（queue_ahead 全口径）→ 既有回归链
# （staging51/fixes51/并发/watcher/staging52/脚本/staging53）
# → 进程内直测（question 本体 + staging51 配额直测）。
# 每段独立全新 DB；隔离端口 18799。
# 注：staging53 的迁移/负载套件针对 question 专项，本片无 schema 变更，
# 不纳入（理由见 REVIEW54.md）。
set -u
cd "$(dirname "$0")"
PY=~/workspace/pack/hub/.venv/bin/python

kill_port() {
  local pid
  pid=$(ss -ltnp 2>/dev/null | grep ":$1 " | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
  if [ -n "${pid:-}" ]; then kill "$pid" 2>/dev/null; sleep 1; fi
}

fresh_db() {
  kill_port 18799
  rm -f pack.db pack.db-wal pack.db-shm .dispatcher_token .room_watch_state.json \
        test_fixtures.json
  rm -rf attachments __pycache__
}

start_hub() {
  PACK_HUB_PORT=18799 nohup $PY hub.py serve > "server-$1.log" 2>&1 &
  for i in $(seq 1 60); do ss -ltn 2>/dev/null | grep -q ':18799 ' && break; sleep 0.3; done
}

# ---- 第一段：staging54 新套件 ----
fresh_db
start_hub 54a
$PY test_staging54.py; RC54=$?
kill_port 18799

# ---- 第二段：既有回归链（全新 DB） ----
fresh_db
start_hub 54b
$PY test_staging51.py; RC1=$?
$PY test_fixes51.py; RC6=$?
$PY test_concurrency.py; RC2=$?
$PY test_watcher.py; RC3=$?
$PY test_staging52.py; RC7=$?
$PY test_agent_script.py; RC8=$?
kill_port 18799

# staging53 套件的夹具成员名（alice/bob）与 51/52 套件同名，必须独享
# 全新 DB（staging53 自家 run_tests.sh 亦如此分段）。
fresh_db
start_hub 54c
$PY test_staging53.py; RC53=$?
kill_port 18799

# ---- 第三段：进程内直测 ----
$PY test_question_state.py; RCQS=$?
$PY test_direct.py; RC5=$?

echo "== staging54=$RC54 | main51=$RC1 fixes=$RC6 concurrency=$RC2 watcher=$RC3 inbox52=$RC7 script=$RC8 staging53=$RC53 | qstate=$RCQS direct=$RC5"
[ $RC54 -eq 0 ] && [ $RC1 -eq 0 ] && [ $RC6 -eq 0 ] && [ $RC2 -eq 0 ] && \
[ $RC3 -eq 0 ] && [ $RC7 -eq 0 ] && [ $RC8 -eq 0 ] && [ $RC53 -eq 0 ] && \
[ $RCQS -eq 0 ] && [ $RC5 -eq 0 ]
