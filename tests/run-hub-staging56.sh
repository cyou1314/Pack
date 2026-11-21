#!/usr/bin/env bash
# Run the staging56 aggregate regression from a disposable copy.
# The historical suite is intentionally preserved byte-for-byte in Git.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE="$ROOT/tests/hub-staging56"
PY="${PY:-python3}"
PY="$(command -v "$PY" || true)"
[[ -x "$PY" ]] || { echo "ERROR: Python interpreter not found" >&2; exit 2; }
[[ -d "$SOURCE" ]] || { echo "ERROR: missing staging56 source: $SOURCE" >&2; exit 2; }
command -v ss >/dev/null || { echo "ERROR: ss (iproute2) is required" >&2; exit 2; }
command -v node >/dev/null || { echo "ERROR: Node.js is required" >&2; exit 2; }

# The historical runner uses fixed ports and its cleanup helper terminates any
# listener on those ports. Refuse to run if any port is occupied; never kill an
# unknown process, especially a developer's or production Hub.
"$PY" - <<'PY'
import socket, sys
ports = (18795, 18796, 18798)
busy = []
for port in ports:
    with socket.socket() as s:
        s.settimeout(0.2)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            busy.append(port)
if busy:
    print("ERROR: refusing to run; ports already in use:", ", ".join(map(str, busy)), file=sys.stderr)
    raise SystemExit(3)
PY

TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/pack-staging56.XXXXXX")"
cleanup() { rm -rf "$TEMP_ROOT"; }
trap cleanup EXIT INT TERM HUP
WORK="$TEMP_ROOT/hub-staging56"
cp -a "$SOURCE" "$WORK"

# Repoint hard-coded VM interpreter paths only inside the disposable copy.
PACK_TEST_PY="$PY" PACK_TEST_WORK="$WORK" "$PY" - <<'PY'
import os
from pathlib import Path
work = Path(os.environ["PACK_TEST_WORK"])
python = os.environ["PACK_TEST_PY"]
for path in work.rglob("*"):
    if not path.is_file():
        continue
    try:
        source = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    updated = source.replace(
        'PY=~/workspace/pack/hub/.venv/bin/python',
        'PY="${PACK_TEST_PY}"',
    )
    updated = updated.replace(
        'VENV_PY = os.path.expanduser("~/workspace/pack/hub/.venv/bin/python")',
        'VENV_PY = sys.executable',
    )
    if updated != source:
        path.write_text(updated, encoding="utf-8")
PY

export PYTHONUNBUFFERED=1
export PACK_TEST_PY="$PY"
cd "$WORK"
echo "Running staging56 in disposable directory: $WORK"
echo "Python: $PY"
bash ./run_tests.sh