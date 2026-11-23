#!/usr/bin/env python3
"""Print a read-only Agent Registry shadow comparison report."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
import sys

HUB_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(HUB_DIR))

from services.agent_registry_shadow import build_shadow_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=HUB_DIR / "pack.db",
        help="Hub SQLite database path (opened read-only)",
    )
    args = parser.parse_args()
    connection = None
    try:
        uri = args.db.expanduser().resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        report = build_shadow_report(connection)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        if report.get("ok"):
            return 0
        return 2 if report.get("ready") else 3
    except Exception as error:
        print(json.dumps({
            "ok": False,
            "error": type(error).__name__,
        }, ensure_ascii=False, sort_keys=True))
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
