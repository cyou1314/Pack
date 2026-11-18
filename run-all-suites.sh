#!/usr/bin/env bash
# Pack 当前 staging 总回归。测试全部在临时目录执行，不修改仓库里的 staging 副本。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON:-python3}"
HUB_SOURCE="${PACK_HUB_SOURCE:-$ROOT/hub/hub.py}"
KEEP_TEMP="${PACK_KEEP_TEST_TEMP:-0}"
SUMMARY="$ROOT/regression-summary.txt"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/pack-regression.XXXXXX")"
STATUS=0

cleanup() {
  if [ "$KEEP_TEMP" = "1" ]; then
    echo "test temp kept: $TMP_ROOT"
  else
    rm -rf "$TMP_ROOT"
  fi
}
trap cleanup EXIT INT TERM

: > "$SUMMARY"
echo "Pack regression" | tee -a "$SUMMARY"
echo "hub under test: $HUB_SOURCE" | tee -a "$SUMMARY"
echo "temp root: $TMP_ROOT" | tee -a "$SUMMARY"

run_case() {
  stage="$1"
  test_file="$2"
  port="$3"
  needs_server="$4"
  src="$ROOT/hub-staging$stage"
  work="$TMP_ROOT/staging$stage"

  [ -d "$src" ] || { echo "FAIL staging$stage: missing directory" | tee -a "$SUMMARY"; STATUS=1; return; }
  [ -f "$src/$test_file" ] || { echo "FAIL staging$stage: missing $test_file" | tee -a "$SUMMARY"; STATUS=1; return; }

  mkdir -p "$work"
  cp -R "$src/." "$work/"
  cp "$HUB_SOURCE" "$work/hub.py"

  if [ "$needs_server" = "1" ]; then
    rm -f "$work/pack.db" "$work/pack.db-wal" "$work/pack.db-shm" "$work/.dispatcher_token"
    (
      cd "$work"
      PACK_HUB_PORT="$port" "$PY" hub.py serve >serve.log 2>&1
    ) &
    server_pid=$!
    server_ok=0
    for _ in $(seq 1 50); do
      if "$PY" - "$port" <<'PY'
import socket, sys
port = int(sys.argv[1])
s = socket.socket()
s.settimeout(0.5)
try:
    s.connect(("127.0.0.1", port))
except OSError:
    raise SystemExit(1)
finally:
    s.close()
raise SystemExit(0)
PY
      then
        server_ok=1
        break
      fi
      sleep 0.2
    done
    if [ "$server_ok" -ne 1 ]; then
      echo "FAIL staging$stage: hub did not start" | tee -a "$SUMMARY"
      cat "$work/serve.log" >&2 || true
      kill "$server_pid" 2>/dev/null || true
      wait "$server_pid" 2>/dev/null || true
      STATUS=1
      return
    fi
  fi

  set +e
  (cd "$work" && "$PY" "$test_file") >"$work/test-output.log" 2>&1
  rc=$?
  set -e

  if [ "$needs_server" = "1" ]; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi

  cat "$work/test-output.log"
  if [ "$rc" -eq 0 ]; then
    echo "PASS staging$stage" | tee -a "$SUMMARY"
  else
    echo "FAIL staging$stage rc=$rc" | tee -a "$SUMMARY"
    STATUS=1
  fi
}

run_case 38 test_week_done.py 8838 1
run_case 39 test_attachments.py 8839 1
run_case 40 test_hardening.py 8840 1
run_case 41 test_w4_handler.py 0 0
run_case 42 test_tasklist_offset.py 8842 1
run_case 43 test_kind_whitelist.py 8843 1
run_case 44 test_w5_template.py 0 0
run_case 45 test_orphans.py 8845 1
run_case 46 test_complete_attach.py 8846 1

echo "================ SUMMARY ================"
cat "$SUMMARY"
if [ "$STATUS" -eq 0 ]; then
  echo "REGRESSION: ALL GREEN"
else
  echo "REGRESSION: FAILURES"
fi
exit "$STATUS"
