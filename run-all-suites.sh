#!/usr/bin/env bash
# Pack regression runner.
# Every suite runs from a disposable copy. The candidate hub.py is copied into
# that copy only; tracked staging directories are never overwritten.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
if [ -z "${PY:-}" ]; then
  PY="$ROOT/hub/.venv/bin/python"
fi
HUB="$ROOT/hub/hub.py"
FULL=0
while (($#)); do
  case "$1" in
    --full) FULL=1; shift ;;
    --hub)
      [ "$#" -ge 2 ] || { echo "ERROR: --hub requires a path" >&2; exit 2; }
      HUB="$2"
      shift 2
      ;;
    --list)
      echo "default suites: 15 16 17 18 19 23 24 38 39 40 41 42 43 44 45 46"
      echo "with --full:    default suites + 21 (adversarial / pressure)"
      exit 0
      ;;
    -h|--help)
      echo "Usage: PY=/path/to/python ./run-all-suites.sh [--full] [--hub /path/to/hub.py] [--list]"
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

[[ -x "$PY" ]] || { echo "ERROR: Python interpreter not executable: $PY" >&2; exit 2; }
[[ -f "$HUB" ]] || { echo "ERROR: Hub source not found: $HUB" >&2; exit 2; }
[[ "$HUB" = /* ]] || HUB="$ROOT/$HUB"

export PACK_TEST_PY="$PY"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/pack-regression.XXXXXX")"
SUMMARY="$ROOT/regression-summary.txt"
KEEP_TEMP="${PACK_KEEP_TEST_TEMP:-0}"
: > "$SUMMARY"

ACTIVE_GROUPS=()
STARTED_PID=""
TOTAL_FAIL=0

log() {
  printf '%s\n' "$*" | tee -a "$SUMMARY"
}

port_free() {
  "$PY" - "$1" <<'PY'
import socket, sys
s = socket.socket()
s.settimeout(0.25)
try:
    rc = s.connect_ex(("127.0.0.1", int(sys.argv[1])))
finally:
    s.close()
raise SystemExit(1 if rc == 0 else 0)
PY
}

stop_group() {
  local pgid="$1"
  # The launcher calls setsid() before exec. All suite descendants inherit this
  # process group, so signal the whole group rather than only the shell PID.
  kill -TERM -- "-$pgid" 2>/dev/null || true
  local i
  for ((i=0; i<40; i++)); do
    if ! kill -0 -- "-$pgid" 2>/dev/null; then
      break
    fi
    sleep 0.1
  done
  kill -KILL -- "-$pgid" 2>/dev/null || true
  wait "$pgid" 2>/dev/null || true
}

forget_group() {
  local target="$1"
  local pgid
  local -a kept=()
  for pgid in "${ACTIVE_GROUPS[@]}"; do
    [[ "$pgid" == "$target" ]] || kept+=("$pgid")
  done
  ACTIVE_GROUPS=("${kept[@]}")
}

cleanup() {
  local rc=$?
  trap - EXIT INT TERM HUP
  local pgid
  if [ "${#ACTIVE_GROUPS[@]}" -gt 0 ]; then
    for pgid in "${ACTIVE_GROUPS[@]}"; do
      stop_group "$pgid"
    done
  fi
  if [ "$KEEP_TEMP" = "1" ]; then
    echo "Temporary regression files retained at: $TMP_ROOT" >&2
  else
    rm -rf "$TMP_ROOT"
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

prepare_stage() {
  local stage="$1"
  local with_hub="$2"
  local source="$ROOT/hub-staging$stage"
  local work="$TMP_ROOT/hub-staging$stage"
  [[ -d "$source" ]] || { echo "ERROR: missing $source" >&2; return 1; }
  cp -a "$source" "$work"

  if [ "$with_hub" = "1" ]; then
    # Replace only the disposable copy. The source staging hub.py is untouched.
    cp "$HUB" "$work/hub.py"
  fi

  rm -f "$work/pack.db" "$work/pack.db-wal" "$work/pack.db-shm" \
    "$work/.dispatcher_token" "$work/tokens.json" "$work/.room_watch_state.json" \
    "$work/room-watch.log" "$work/serve.log" "$work/watch.log" \
    "$work/regression-run.log" "$work/legacy-run.log" \
    "$work"/test-output*.txt "$work"/test-output-round*.txt
  rm -rf "$work/attachments" "$work/backups"

  # The historical suites contain absolute paths from the original VM. Rewrite
  # those references in the disposable copy only; also honor this runner's PY.
  PACK_TEST_STAGE="$stage" PACK_TEST_STAGE_ROOT="$work" \
  PACK_TEST_ROOT="$ROOT" PACK_TEST_PY="$PY" "$PY" - <<'PY'
import os
from pathlib import Path

stage = os.environ["PACK_TEST_STAGE"]
work = os.environ["PACK_TEST_STAGE_ROOT"]
root = os.environ["PACK_TEST_ROOT"]
python = os.environ["PACK_TEST_PY"]
old_stage = f"/home/hatch/workspace/pack/hub-staging{stage}"
old_skills = "/home/hatch/workspace/pack/repo-mirror/skills"
old_python = "/home/hatch/workspace/pack/hub/.venv/bin/python"

for path in Path(work).rglob("*"):
    if not path.is_file():
        continue
    try:
        content = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    updated = content.replace(old_stage, work)
    updated = updated.replace(old_skills, os.path.join(root, "skills"))
    updated = updated.replace("PY=" + old_python, 'PY="$PACK_TEST_PY"')
    if updated != content:
        path.write_text(updated, encoding="utf-8")
PY
  printf '%s\n' "$work"
}

start_group() {
  local cwd="$1"
  local helper="$2"
  local output="$3"
  shift 3
  local previous="$PWD"
  cd "$cwd"
  # Python creates a new session/process group and then execs bash in-place.
  # $! is therefore also the process-group ID used for complete cleanup.
  "$PY" -c 'import os,sys; os.setsid(); os.execv("/bin/bash", ["/bin/bash", *sys.argv[1:]])' \
    "$helper" "$@" > "$output" 2>&1 &
  STARTED_PID=$!
  cd "$previous"
  ACTIVE_GROUPS+=("$STARTED_PID")
}

run_group() {
  local cwd="$1"
  local helper="$2"
  local output="$3"
  shift 3
  start_group "$cwd" "$helper" "$output" "$@"
  local pid="$STARTED_PID"
  local rc=0
  if wait "$pid"; then rc=0; else rc=$?; fi
  # The leader may have exited while a child server/watcher is still alive.
  # Always terminate the complete group before moving to the next suite.
  stop_group "$pid"
  forget_group "$pid"
  if [ -s "$output" ]; then cat "$output"; fi
  return "$rc"
}

write_server_helper() {
  local work="$1"
  cat > "$work/.pack-run-server-test.sh" <<'BASH'
#!/usr/bin/env bash
set -Eeuo pipefail
PORT="$1"
TEST="$2"
shift 2

rm -f pack.db pack.db-wal pack.db-shm .dispatcher_token
rm -rf attachments

PACK_HUB_PORT="$PORT" "$PACK_TEST_PY" hub.py serve > serve.log 2>&1 &
SERVER_PID=$!

ready=0
for i in $(seq 1 80); do
  if "$PACK_TEST_PY" - "$PORT" <<'PY'
import socket, sys
s = socket.socket()
s.settimeout(0.25)
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
except OSError:
    raise SystemExit(1)
finally:
    s.close()
raise SystemExit(0)
PY
  then
    ready=1
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then break; fi
  sleep 0.25
done

if [ "$ready" -ne 1 ]; then
  echo "SERVER_START_FAILED port=$PORT"
  cat serve.log >&2 || true
  exit 70
fi

token_ready=0
for i in $(seq 1 30); do
  if [ -s .dispatcher_token ]; then token_ready=1; break; fi
  sleep 0.1
done
if [ "$token_ready" -ne 1 ]; then
  echo "DISPATCHER_TOKEN_NOT_CREATED"
  cat serve.log >&2 || true
  exit 71
fi

"$PACK_TEST_PY" "$TEST" "$@"
BASH
  chmod +x "$work/.pack-run-server-test.sh"
}

write_local_helper() {
  local work="$1"
  cat > "$work/.pack-run-local-test.sh" <<'BASH'
#!/usr/bin/env bash
set -Eeuo pipefail
TEST="$1"
shift
# Import-only room_watch tests need a token file at module import time.
# This is a disposable placeholder, never a real Hub credential.
printf '%s\n' 'pack-test-only-placeholder-not-a-real-token' > .dispatcher_token
chmod 600 .dispatcher_token
"$PACK_TEST_PY" "$TEST" "$@"
BASH
  chmod +x "$work/.pack-run-local-test.sh"
}

write_stage24_helper() {
  local work="$1"
  cat > "$work/.pack-run-stage24.sh" <<'BASH'
#!/usr/bin/env bash
set -Eeuo pipefail
PORT=8822
rm -f pack.db pack.db-wal pack.db-shm .dispatcher_token \
  .room_watch_state.json tokens.json regression-run.log
rm -rf attachments

PACK_HUB_PORT="$PORT" "$PACK_TEST_PY" hub.py serve > serve.log 2>&1 &
SERVER_PID=$!

ready=0
for i in $(seq 1 80); do
  if "$PACK_TEST_PY" - "$PORT" <<'PY'
import socket, sys
s = socket.socket()
s.settimeout(0.25)
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
except OSError:
    raise SystemExit(1)
finally:
    s.close()
raise SystemExit(0)
PY
  then
    ready=1
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then break; fi
  sleep 0.25
done
if [ "$ready" -ne 1 ]; then
  echo "SERVER_START_FAILED port=$PORT"
  cat serve.log >&2 || true
  exit 70
fi

token_ready=0
for i in $(seq 1 30); do
  if [ -s .dispatcher_token ]; then token_ready=1; break; fi
  sleep 0.1
done
[ "$token_ready" -eq 1 ] || { echo "DISPATCHER_TOKEN_NOT_CREATED"; exit 71; }

"$PACK_TEST_PY" rehearse_combo.py seed > seed.log 2>&1
cat seed.log >> regression-run.log

PACK_WATCH_URL="http://127.0.0.1:$PORT/mcp" \
  "$PACK_TEST_PY" room_watch.py > watch.log 2>&1 &
WATCH_PID=$!

watch_ready=0
for i in $(seq 1 80); do
  if "$PACK_TEST_PY" - .room_watch_state.json <<'PY'
import json, sys
try:
    state = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if "room-b" in state.get("rooms", {}) else 1)
PY
  then
    watch_ready=1
    break
  fi
  if ! kill -0 "$WATCH_PID" 2>/dev/null; then
    echo "WATCHER_EXITED_BEFORE_INITIALIZATION"
    cat watch.log >&2 || true
    exit 72
  fi
  sleep 0.5
done
if [ "$watch_ready" -ne 1 ]; then
  echo "WATCHER_INITIALIZATION_TIMEOUT"
  cat watch.log >&2 || true
  exit 73
fi

"$PACK_TEST_PY" rehearse_combo.py verify > verify.log 2>&1
cat verify.log >> regression-run.log
BASH
  chmod +x "$work/.pack-run-stage24.sh"
}

record_result() {
  local label="$1"
  local status="$2"
  local detail="$3"
  if [ "$status" -eq 0 ]; then
    log "PASS $label $detail"
  else
    log "FAIL $label $detail"
    TOTAL_FAIL=1
  fi
}

run_legacy_stage() {
  local stage="$1"
  local port="$2"
  local work
  local rc=0
  local status=0
  work="$(prepare_stage "$stage" 1)"

  if ! port_free "$port"; then
    record_result "staging$stage" 1 "port $port is already occupied; refusing to attach to an unknown process"
    return
  fi
  if [ ! -f "$work/run_all.sh" ]; then
    record_result "staging$stage" 1 "existing run_all.sh is missing; suite was not skipped"
    return
  fi

  log "--- staging$stage: existing run_all.sh, isolated copy, port $port"
  if run_group "$work" "$work/run_all.sh" "$work/legacy-run.log"; then rc=0; else rc=$?; fi

  local output found=0
  local -a outputs=("$work"/test-output*.txt)
  for output in "${outputs[@]}"; do
    [ -f "$output" ] || continue
    found=1
    {
      echo "== $(basename "$output") =="
      cat "$output"
    } >> "$SUMMARY"
    if grep -E 'FAIL' "$output" | grep -v 'no FAIL lines' >/dev/null; then status=1; fi
    if grep -Eq '^rc(\([^)]*\))?=([1-9][0-9]*)$' "$output"; then status=1; fi
  done
  if [ "$found" -ne 1 ] || [ "$rc" -ne 0 ]; then status=1; fi

  case "$stage" in
    15)
      for marker in \
        'rc(test_skills.py)=0' 'rc(test_review.py)=0' \
        'rc(test_functional.py)=0' 'rc(test_more.py)=0' \
        'rc(test_concurrency.py)=0' 'rc(test_decision_kind.py)=0'; do
        grep -Fq "$marker" "$work/test-output.txt" 2>/dev/null || status=1
      done
      ;;
    16)
      grep -Fq 'rc(test_h123)=0' "$work/test-output-round1.txt" 2>/dev/null || status=1
      ;;
    17|18|19)
      grep -Eq '^rc=0$' "$work/test-output-round1.txt" 2>/dev/null || status=1
      ;;
  esac

  if ! port_free "$port"; then
    status=1
    log "PORT_LEAK staging$stage port=$port remains listening after process-group cleanup"
  fi
  record_result "staging$stage" "$status" "run_all.sh rc=$rc; server process group terminated"
}

run_server_stage() {
  local stage="$1"
  local port="$2"
  local test="$3"
  local work
  local rc=0
  local status=0
  work="$(prepare_stage "$stage" 1)"
  write_server_helper "$work"

  if ! port_free "$port"; then
    record_result "staging$stage" 1 "port $port is already occupied; refusing to attach to an unknown process"
    return
  fi

  log "--- staging$stage: hub.py serve + $test, isolated copy, port $port"
  if run_group "$work" "$work/.pack-run-server-test.sh" "$work/regression-run.log" "$port" "$test"; then
    rc=0
  else
    rc=$?
  fi
  [ "$rc" -eq 0 ] || status=1
  if ! port_free "$port"; then
    status=1
    log "PORT_LEAK staging$stage port=$port remains listening after process-group cleanup"
  fi
  record_result "staging$stage" "$status" "server process group terminated; test=$test rc=$rc"
}

run_local_stage() {
  local stage="$1"
  local test="$2"
  local work
  local rc=0
  work="$(prepare_stage "$stage" 0)"
  write_local_helper "$work"

  log "--- staging$stage: local import-only test, dummy token in disposable copy"
  if run_group "$work" "$work/.pack-run-local-test.sh" "$work/regression-run.log" "$test"; then
    rc=0
  else
    rc=$?
  fi
  record_result "staging$stage" "$rc" "no server; test=$test; placeholder token only"
}

run_stage24() {
  local work
  local rc=0
  local status=0
  work="$(prepare_stage 24 1)"
  write_stage24_helper "$work"

  if ! port_free 8822; then
    record_result "staging24" 1 "port 8822 is already occupied; refusing to attach to an unknown process"
    return
  fi

  log "--- staging24: Hub + seed, watcher initialization barrier, verify; isolated copy, port 8822"
  if run_group "$work" "$work/.pack-run-stage24.sh" "$work/stage24-group.log"; then
    rc=0
  else
    rc=$?
  fi
  [ "$rc" -eq 0 ] || status=1
  if grep -E 'FAIL' "$work/regression-run.log" >/dev/null 2>&1; then status=1; fi
  if ! port_free 8822; then
    status=1
    log "PORT_LEAK staging24 port=8822 remains listening after process-group cleanup"
  fi
  record_result "staging24" "$status" "Hub and watcher process group terminated; seed/verify rc=$rc"
}

log "Pack regression runner"
log "base: $ROOT"
log "hub under test: $HUB"
log "python: $PY"
log "temporary root: $TMP_ROOT"
log "default suite order: 15 16 17 18 19 23 24 38 39 40 41 42 43 44 45 46"

run_legacy_stage 15 8813
run_legacy_stage 16 8814
run_legacy_stage 17 8815
run_legacy_stage 18 8816
run_legacy_stage 19 8817

run_server_stage 23 8821 test_s2_walk.py
run_stage24

run_server_stage 38 8838 test_week_done.py
run_server_stage 39 8839 test_attachments.py
run_server_stage 40 8840 test_hardening.py
run_local_stage 41 test_w4_handler.py
run_server_stage 42 8842 test_tasklist_offset.py
run_server_stage 43 8843 test_kind_whitelist.py
run_local_stage 44 test_w5_template.py
run_server_stage 45 8845 test_orphans.py
run_server_stage 46 8846 test_complete_attach.py

if [ "$FULL" -eq 1 ]; then
  run_server_stage 21 8819 test_adversarial.py
  log "--full appended staging21 adversarial/pressure suite"
fi

log "================ SUMMARY ================"
cat "$SUMMARY"
if [ "$TOTAL_FAIL" -eq 0 ]; then
  log "REGRESSION: ALL GREEN (VM acceptance must still run twice)"
else
  log "REGRESSION: FAILURES"
fi

exit "$TOTAL_FAIL"
