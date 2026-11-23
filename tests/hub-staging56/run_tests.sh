#!/usr/bin/env bash
# staging56: access_mode (PORT-ACCESSMODE-BRIEF). Fresh DB per segment,
# isolated port 18798 (migration56 brings its own 18795/18796).
# Segments: new suite -> migration -> 51/52 regression chain ->
# staging53 (+question_state) -> staging54 -> staging55 -> direct ->
# load53 probe. Copied suites have their tool-count assertions bumped
# 74 -> 75 (same precedent as staging52's copy of test_staging51).
set -u
cd "$(dirname "$0")"
PY="${PY:-$HOME/workspace/pack/hub/.venv/bin/python}"
if [[ "$PY" != */* ]]; then
  PY="$(command -v "$PY" || true)"
fi
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3 || command -v python || true)"
fi
if [[ -z "$PY" || ! -x "$PY" ]]; then
  echo "ERROR: no usable Python interpreter found; set PY=/path/to/python" >&2
  exit 2
fi
export PACK_TEST_PY="$PY"

port_ready() {
  "$PY" - "$1" <<'PY'
import socket, sys
s = socket.socket()
s.settimeout(0.25)
try:
    raise SystemExit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)
finally:
    s.close()
PY
}

kill_port() {
  local port="$1"
  local pids=""
  if command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  fi
  if [[ -z "$pids" ]] && command -v ss >/dev/null 2>&1; then
    pids="$(ss -ltnp 2>/dev/null | grep ":$port " | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u || true)"
  fi
  for pid in $pids; do
    kill "$pid" 2>/dev/null || true
  done
  for _ in $(seq 1 20); do
    port_ready "$port" || return 0
    sleep 0.1
  done
  echo "ERROR: listener on port $port did not stop (pids: ${pids:-unknown})" >&2
  return 1
}

fresh_db() {
  kill_port 18798 || exit 72
  rm -f pack.db pack.db-wal pack.db-shm .dispatcher_token .room_watch_state.json \
        test_fixtures.json
  rm -rf attachments __pycache__
}

start_hub() {
  PACK_HUB_PORT=18798 nohup "$PY" hub.py serve > "server-$1.log" 2>&1 &
  for i in $(seq 1 60); do
    port_ready 18798 && break
    sleep 0.3
  done
  if ! port_ready 18798; then
    echo "SERVER_START_FAILED segment=$1 port=18798" >&2
    cat "server-$1.log" >&2 || true
    exit 70
  fi
  for i in $(seq 1 30); do
    [[ -s .dispatcher_token ]] && break
    sleep 0.1
  done
  if [[ ! -s .dispatcher_token ]]; then
    echo "DISPATCHER_TOKEN_NOT_CREATED segment=$1" >&2
    cat "server-$1.log" >&2 || true
    exit 71
  fi
}

# ---- segment 1: staging56 new suite ----
fresh_db
start_hub 56a
"$PY" test_staging56.py; RC56=$?
kill_port 18798

# ---- segment 2: migration (self-contained dual server) ----
"$PY" test_migration56.py; RCMIG=$?

# ---- segment 3: staging51/52 regression chain ----
fresh_db
start_hub 56b
"$PY" test_staging51.py; RC1=$?
"$PY" test_fixes51.py; RC6=$?
"$PY" test_concurrency.py; RC2=$?
"$PY" test_watcher.py; RC3=$?
"$PY" test_staging52.py; RC7=$?
"$PY" test_agent_script.py; RC8=$?
kill_port 18798

# ---- segment 4: staging53 + question_state ----
fresh_db
start_hub 56c
"$PY" test_staging53.py; RC53=$?
"$PY" test_question_state.py; RCQS=$?
kill_port 18798

# ---- segment 5: staging54 ----
fresh_db
start_hub 56d
"$PY" test_staging54.py; RC54=$?
kill_port 18798

# ---- segment 6: staging55 ----
fresh_db
start_hub 56e
"$PY" test_staging55.py; RC55=$?
kill_port 18798

# ---- segment 7: in-process direct suite ----
"$PY" test_direct.py; RC5=$?

# ---- segment 8: load probe (fresh DB) ----
fresh_db
start_hub 56f
"$PY" test_load53.py; RC9=$?
kill_port 18798

echo "== staging56=$RC56 migration=$RCMIG | main51=$RC1 fixes=$RC6 concurrency=$RC2 watcher=$RC3 inbox52=$RC7 script=$RC8 | staging53=$RC53 qstate=$RCQS staging54=$RC54 staging55=$RC55 | direct=$RC5 load=$RC9"
[ "$RC56" = 0 ] && [ "$RCMIG" = 0 ] && [ "$RC1" = 0 ] && [ "$RC6" = 0 ] && \
[ "$RC2" = 0 ] && [ "$RC3" = 0 ] && [ "$RC7" = 0 ] && [ "$RC8" = 0 ] && \
[ "$RC53" = 0 ] && [ "$RCQS" = 0 ] && [ "$RC54" = 0 ] && [ "$RC55" = 0 ] && \
[ "$RC5" = 0 ] && [ "$RC9" = 0 ]
