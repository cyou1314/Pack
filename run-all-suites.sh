#!/usr/bin/env bash
# Pack 中枢一键总回归：把指定 hub.py（默认线上正本）依次塞进各 staging
# 套件跑一遍，跑完恢复各套件自己的 hub.py 副本，最后汇总 RESULT 行。
# 用法：
#   ./run-all-suites.sh [--full] [--hub /path/to/hub.py] [--list]
# 默认套件：15（skills/review/functional/more/concurrency/decision_kind）、
# 16（h123）、17（summary）、18（templates）、19（schedule_relay）、
# 23（S2 契约）、24（组合演练）。--full 追加 21（对抗+压力，约 16 分钟）。
# 每个新中枢增量上线前跑一遍；全绿才许部署（部署纪律见 SPEC 附录 H）。
set -u
PACK=/home/hatch/workspace/pack
PY=$PACK/hub/.venv/bin/python
HUB=$PACK/hub/hub.py
FULL=0
for a in "$@"; do
  case "$a" in
    --full) FULL=1 ;;
    --hub) shift; HUB="$1" ;;
    --list) echo "suites: 15 16 17 18 19 23 24 (+21 with --full)"; exit 0 ;;
  esac
done
SUMMARY=$PACK/regression-summary.txt
: > "$SUMMARY"
echo "hub under test: $HUB" | tee -a "$SUMMARY"

swap_in() {  # $1=dir
  cp "$1/hub.py" "$1/hub.py.regression-bak"
  cp "$HUB" "$1/hub.py"
}
swap_out() { # $1=dir
  [ -f "$1/hub.py.regression-bak" ] && mv "$1/hub.py.regression-bak" "$1/hub.py"
}
record() { # $1=label $2=output-file
  echo "== $1" >> "$SUMMARY"
  grep -E "RESULT|rc\(|passed" "$2" >> "$SUMMARY" 2>/dev/null || echo "(no RESULT lines)" >> "$SUMMARY"
}

for d in 15 16 17 18 19; do
  dir=$PACK/hub-staging$d
  echo "--- staging$d ..."
  swap_in "$dir"
  (cd "$dir" && bash run_all.sh > regression-run.log 2>&1)
  swap_out "$dir"
  # run_all 的明细在各套件自己的输出文件里，统一摘 RESULT/rc 行
  cat "$dir"/test-output*.txt "$dir"/test-output-round*.txt 2>/dev/null | grep -E "RESULT|rc\(" > "$dir/regression-result.txt" || true
  record "staging$d" "$dir/regression-result.txt"
done

run_boot_suite() { # $1=dir $2=port $3=testfile [extra args]
  dir=$1; port=$2; test=$3; shift 3
  echo "--- $dir ..."
  swap_in "$dir"
  (cd "$dir"
   rm -f pack.db pack.db-wal pack.db-shm .dispatcher_token
   PACK_HUB_PORT=$port $PY hub.py serve > serve.log 2>&1 &
   SRV=$!
   for i in $(seq 1 25); do curl -s -o /dev/null http://127.0.0.1:$port/mcp && break; sleep 1; done
   $PY "$test" "$@" > regression-run.log 2>&1
   kill $SRV 2>/dev/null; wait $SRV 2>/dev/null)
  swap_out "$dir"
  record "$dir" "$dir/regression-run.log"
}

run_boot_suite $PACK/hub-staging23 8821 test_s2_walk.py

# staging24 组合演练需要 watcher 参与，单独走它的两阶段流程
dir=$PACK/hub-staging24
echo "--- staging24 ..."
swap_in "$dir"
(cd "$dir"
 rm -f pack.db pack.db-wal pack.db-shm .dispatcher_token .room_watch_state.json tokens.json
 PACK_HUB_PORT=8822 $PY hub.py serve > serve.log 2>&1 &
 HPID=$!
 for i in $(seq 1 25); do curl -s -o /dev/null http://127.0.0.1:8822/mcp && break; sleep 1; done
 $PY rehearse_combo.py seed >> regression-run.log 2>&1
 PACK_WATCH_URL=http://127.0.0.1:8822/mcp $PY room_watch.py > watch.log 2>&1 &
 WPID=$!
 sleep 3
 $PY rehearse_combo.py verify >> regression-run.log 2>&1
 kill $WPID $HPID 2>/dev/null)
swap_out "$dir"
record "staging24" "$dir/regression-run.log"

if [ "$FULL" = "1" ]; then
  run_boot_suite $PACK/hub-staging21 8819 test_adversarial.py
fi

echo "================ SUMMARY ================"
cat "$SUMMARY"
grep -qE "fail=[1-9]|FAIL " "$SUMMARY" && echo "REGRESSION: HAS FAILURES" || echo "REGRESSION: ALL GREEN (check counts above)"
