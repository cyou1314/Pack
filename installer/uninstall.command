#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/python/bin/python3" "$DIR/manager.py" uninstall
