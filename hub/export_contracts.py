#!/usr/bin/env python3
"""Export and verify Pack Hub's externally observable contracts.

The MCP tool snapshot is taken from FastMCP's registered tool catalog, not from
hand-maintained declarations. SQLite snapshots are produced by running the real
Hub initializer against disposable fresh and legacy databases.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import inspect
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

HUB_DIR = Path(__file__).resolve().parent
CONTRACT_DIR = HUB_DIR / "contracts"
FIXTURE = CONTRACT_DIR / "legacy-v0.sql"
SNAPSHOTS = {
    "mcp-tools.json": "mcp_tools",
    "sqlite-fresh.json": "sqlite_fresh",
    "sqlite-legacy-migrated.json": "sqlite_legacy_migrated",
}


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if dataclasses.is_dataclass(value):
        return _jsonable(dataclasses.asdict(value))
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _jsonable(model_dump(mode="json", by_alias=True, exclude_none=True))
        except TypeError:
            return _jsonable(model_dump())
    legacy_dump = getattr(value, "dict", None)
    if callable(legacy_dump):
        try:
            return _jsonable(legacy_dump(by_alias=True, exclude_none=True))
        except TypeError:
            return _jsonable(legacy_dump())
    raise TypeError(f"unsupported contract value: {type(value).__name__}")


def _field(value: Any, *names: str) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
        return None
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return None


def export_mcp_tools() -> dict[str, Any]:
    sys.path.insert(0, str(HUB_DIR))
    import hub as hub_module

    server = hub_module.build_server()
    registered = server.list_tools()
    if inspect.isawaitable(registered):
        registered = asyncio.run(registered)

    records = []
    for tool in registered:
        name = _field(tool, "name")
        if not isinstance(name, str) or not name:
            raise RuntimeError(f"FastMCP returned a tool without a valid name: {tool!r}")
        # FastMCP 4's registered FunctionTool stores the generated JSON
        # Schema as `parameters`; MCP wire Tool objects expose input_schema.
        input_schema = _field(tool, "parameters", "input_schema", "inputSchema")
        if input_schema is None:
            raw = _jsonable(tool)
            input_schema = raw.get("inputSchema", raw.get("input_schema"))
        if input_schema is None:
            raise RuntimeError(f"FastMCP tool {name!r} has no input schema")
        record = {
            "name": name,
            "description": _field(tool, "description") or "",
            "inputSchema": _jsonable(input_schema),
        }
        output_schema = _field(tool, "output_schema", "outputSchema")
        if output_schema is not None:
            record["outputSchema"] = _jsonable(output_schema)
        annotations = _field(tool, "annotations")
        if annotations is not None:
            record["annotations"] = _jsonable(annotations)
        records.append(record)

    records.sort(key=lambda item: item["name"])
    names = [item["name"] for item in records]
    if len(names) != len(set(names)):
        raise RuntimeError("FastMCP registered duplicate tool names")
    return {
        "format": 1,
        "source": "FastMCP build_server().list_tools()",
        "tool_count": len(records),
        "tools": records,
    }


def _copy_hub(destination: Path) -> Path:
    shutil.copy2(HUB_DIR / "hub.py", destination / "hub.py")
    for package in ("core", "services"):
        package_dir = HUB_DIR / package
        if package_dir.is_dir():
            shutil.copytree(package_dir, destination / package)
    return destination / "hub.py"


def _read_sqlite_contract(db_path: Path, include_migration_assertions: bool) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        table_names = [
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        tables = []
        for table_name in table_names:
            columns = [
                {
                    "name": row["name"],
                    "type": row["type"],
                    "notnull": bool(row["notnull"]),
                    "default": row["dflt_value"],
                    "primary_key_position": row["pk"],
                }
                for row in conn.execute(f'PRAGMA table_info("{table_name}")')
            ]
            indexes = []
            for index in conn.execute(f'PRAGMA index_list("{table_name}")'):
                index_name = index["name"]
                index_columns = [
                    row["name"]
                    for row in conn.execute(f'PRAGMA index_info("{index_name}")')
                ]
                index_sql = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
                    (index_name,),
                ).fetchone()
                indexes.append({
                    "name": index_name,
                    "unique": bool(index["unique"]),
                    "origin": index["origin"],
                    "partial": bool(index["partial"]),
                    "columns": index_columns,
                    "sql": index_sql["sql"] if index_sql else None,
                })
            indexes.sort(key=lambda item: item["name"])
            foreign_keys = [
                {
                    "table": row["table"],
                    "from": row["from"],
                    "to": row["to"],
                    "on_update": row["on_update"],
                    "on_delete": row["on_delete"],
                }
                for row in conn.execute(f'PRAGMA foreign_key_list("{table_name}")')
            ]
            tables.append({
                "name": table_name,
                "columns": columns,
                "indexes": indexes,
                "foreign_keys": foreign_keys,
            })

        triggers = [
            {"name": row["name"], "table": row["tbl_name"], "sql": row["sql"]}
            for row in conn.execute(
                "SELECT name,tbl_name,sql FROM sqlite_master "
                "WHERE type='trigger' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        result: dict[str, Any] = {
            "format": 1,
            "source": "hub.init() on disposable SQLite database",
            "integrity_check": conn.execute("PRAGMA integrity_check").fetchone()[0],
            "tables": tables,
            "triggers": triggers,
        }
        if include_migration_assertions:
            member = conn.execute(
                "SELECT name,token_hash,avatar,provider,login_state,access_mode "
                "FROM members WHERE name='legacy-member'"
            ).fetchone()
            task = conn.execute(
                "SELECT id,title,envelope,fail_class,next_claim_at,sched_at,urgent "
                "FROM tasks WHERE id=9"
            ).fetchone()
            transfer = conn.execute(
                "SELECT status,accepted_at,expires_at FROM task_transfers WHERE id=1"
            ).fetchone()
            message = conn.execute(
                "SELECT id,body,deleted_at,edited_at,reply_to_id,payload "
                "FROM messages WHERE id=1"
            ).fetchone()
            room = conn.execute(
                "SELECT name,archived_at FROM rooms WHERE name='legacy-room'"
            ).fetchone()
            if not all((member, task, transfer, message, room)):
                raise RuntimeError("legacy migration fixture rows were not preserved")
            if member["token_hash"] != "legacy-token-hash" or not member["avatar"]:
                raise RuntimeError("legacy member token was changed or avatar was not backfilled")
            if transfer["status"] != "accepted":
                raise RuntimeError("legacy transfer status was not backfilled to accepted")
            if task["title"] != "legacy task" or message["body"] != "legacy message":
                raise RuntimeError("legacy task/message data was not preserved")
            result["migration_assertions"] = {
                "legacy_member_preserved": True,
                "legacy_member_avatar_backfilled": True,
                "legacy_member_token_hash_preserved": True,
                "legacy_task_preserved": True,
                "legacy_message_preserved": True,
                "legacy_transfer_status_backfilled": transfer["status"],
                "legacy_room_preserved": True,
            }
        return result
    finally:
        conn.close()


def export_sqlite_schema(legacy: bool) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="pack-contract-") as temporary:
        work = Path(temporary)
        hub_script = _copy_hub(work)
        db_path = work / "pack.db"
        if legacy:
            conn = sqlite3.connect(db_path)
            try:
                conn.executescript(FIXTURE.read_text(encoding="utf-8"))
                conn.commit()
            finally:
                conn.close()
        result = subprocess.run(
            [sys.executable, str(hub_script), "init"],
            cwd=work,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            capture_output=True,
            text=True,
            timeout=45,
        )
        if result.returncode:
            raise RuntimeError(
                "Hub init failed while exporting SQLite contract:\n"
                + result.stdout[-4000:]
                + result.stderr[-4000:]
            )
        return _read_sqlite_contract(db_path, include_migration_assertions=legacy)


def export_all() -> dict[str, Any]:
    return {
        "mcp_tools": export_mcp_tools(),
        "sqlite_fresh": export_sqlite_schema(legacy=False),
        "sqlite_legacy_migrated": export_sqlite_schema(legacy=True),
    }


def _render(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="write current snapshots")
    group.add_argument("--check", action="store_true", help="compare current contracts to snapshots")
    group.add_argument("--print", dest="print_output", action="store_true", help="print generated snapshots")
    args = parser.parse_args()

    generated = export_all()
    rendered = {filename: _render(generated[key]) for filename, key in SNAPSHOTS.items()}
    if args.print_output:
        print(_render({filename: json.loads(content) for filename, content in rendered.items()}), end="")
        return 0

    CONTRACT_DIR.mkdir(parents=True, exist_ok=True)
    if args.write:
        for filename, content in rendered.items():
            (CONTRACT_DIR / filename).write_text(content, encoding="utf-8")
            print(f"WROTE {CONTRACT_DIR / filename}")
        return 0

    mismatches = []
    for filename, content in rendered.items():
        path = CONTRACT_DIR / filename
        if not path.is_file():
            mismatches.append(f"MISSING {path}")
            continue
        if path.read_text(encoding="utf-8") != content:
            mismatches.append(f"CHANGED {path}")
    if mismatches:
        for item in mismatches:
            print(item, file=sys.stderr)
        print("Review intentional contract changes, then run: python hub/export_contracts.py --write", file=sys.stderr)
        return 1
    print("PASS MCP tool and SQLite schema snapshots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
