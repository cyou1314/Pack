#!/bin/bash
set -u
cd /home/hatch/workspace/pack/hub-staging19
PY=/home/hatch/workspace/pack/hub/.venv/bin/python
ROUND="${1:-1}"
OUT="test-output-round${ROUND}.txt"
: > "$OUT"
rm -f pack.db pack.db-wal pack.db-shm .dispatcher_token serve.log
PACK_HUB_PORT=8817 $PY hub.py serve > serve.log 2>&1 &
SRV=$!
for i in $(seq 1 25); do curl -s -o /dev/null http://127.0.0.1:8817/mcp && break; sleep 1; done
$PY test_schedule_relay.py >> "$OUT" 2>&1
echo "rc=$?" >> "$OUT"
kill $SRV 2>/dev/null; wait $SRV 2>/dev/null
grep "RESULT" "$OUT"; grep "FAIL" "$OUT" || echo "no FAIL lines"
