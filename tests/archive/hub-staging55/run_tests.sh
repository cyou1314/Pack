#!/usr/bin/env bash
# staging55: room_read_new reply_to_id readback. Fresh DB per segment,
# isolated port 18797. Segments: new suite, staging53 question chain,
# staging54 queue chain (copied suites test this dir's hub.py).
set -u
cd "$(dirname "$0")"
PY=~/workspace/pack/hub/.venv/bin/python

kill_port() {
  local pid
  pid=$(ss -ltnp 2>/dev/null | grep ":$1 " | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)
  if [ -n "${pid:-}" ]; then kill "$pid" 2>/dev/null; sleep 1; fi
}

fresh_db() {
  kill_port 18797
  rm -f pack.db pack.db-wal pack.db-shm .dispatcher_token .room_watch_state.json \
        test_fixtures.json
  rm -rf attachments __pycache__
}

start_hub() {
  PACK_HUB_PORT=18797 nohup $PY hub.py serve > "server-$1.log" 2>&1 &
  for i in $(seq 1 60); do ss -ltn 2>/dev/null | grep -q ':18797 ' && break; sleep 0.3; done
}

fresh_db
start_hub 55a
$PY test_staging55.py; RC55=$?
kill_port 18797

fresh_db
start_hub 55b
$PY test_staging53.py; RC53=$?
$PY test_question_state.py; RCQS=$?
kill_port 18797

fresh_db
start_hub 55c
$PY test_staging54.py; RC54=$?
kill_port 18797

echo "== staging55=$RC55 staging53=$RC53 qstate=$RCQS staging54=$RC54"
[ "$RC55" = 0 ] && [ "$RC53" = 0 ] && [ "$RCQS" = 0 ] && [ "$RC54" = 0 ]
