#!/bin/bash
# staging15：全新 DB + 端口 8813，全套回归 + 本轮专测。
# 阶段：A skills(36) / B review(45) / C functional+more+concurrency（同一 DB 血统，
# 与 staging14 原跑法一致）/ D decision-kind 专测（每阶段除 C 内部外均换全新 DB）。
set -u
cd /home/hatch/workspace/pack/hub-staging15
PY=/home/hatch/workspace/pack/hub/.venv/bin/python
OUT=test-output.txt
: > $OUT

start_server() {
  rm -f pack.db pack.db-wal pack.db-shm .dispatcher_token serve.log
  [ "${1:-}" = "keep" ] && return 0
  PACK_HUB_PORT=8813 $PY hub.py serve > serve.log 2>&1 &
  SRV=$!
  for i in $(seq 1 25); do
    curl -s -o /dev/null http://127.0.0.1:8813/mcp && break
    sleep 1
  done
  echo "server pid: $SRV" >> $OUT
}
stop_server() { kill ${SRV:-} 2>/dev/null; wait ${SRV:-} 2>/dev/null; }
run() { echo "===== $1 =====" >> $OUT; $PY "$1" >> $OUT 2>&1; echo "rc($1)=$?" >> $OUT; }

# A: S1 专测 36
start_server; run test_skills.py; stop_server
# B: S1 复检套件 45
start_server; run test_review.py; stop_server
# C: staging14 功能 78（functional+more）+ 并发 C 系列
start_server; run test_functional.py; run test_more.py; run test_concurrency.py; stop_server
# D: 本轮专测
start_server; run test_decision_kind.py; stop_server
echo "ALL DONE" >> $OUT
