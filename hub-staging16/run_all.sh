#!/bin/bash
# staging16：H1/H2/H3 专测，全新 DB + 端口 8814。
# 用法：run_all.sh [轮次] —— 每轮全新 DB 重跑专测（用户要求多试验几次）。
set -u
cd /home/hatch/workspace/pack/hub-staging16
PY=/home/hatch/workspace/pack/hub/.venv/bin/python
ROUND="${1:-1}"
OUT="test-output-round${ROUND}.txt"
: > "$OUT"
rm -f pack.db pack.db-wal pack.db-shm .dispatcher_token serve.log
PACK_HUB_PORT=8814 $PY hub.py serve > serve.log 2>&1 &
SRV=$!
for i in $(seq 1 25); do curl -s -o /dev/null http://127.0.0.1:8814/mcp && break; sleep 1; done
echo "server pid: $SRV" >> "$OUT"
$PY test_h123.py >> "$OUT" 2>&1
echo "rc(test_h123)=$?" >> "$OUT"
kill $SRV 2>/dev/null; wait $SRV 2>/dev/null
tail -1 "$OUT"
grep -c "  ok " "$OUT" | sed 's/^/ok-count: /'
grep "FAIL" "$OUT" || echo "no FAIL lines"
