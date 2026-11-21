#!/usr/bin/env python3
"""Fail CI when the documented Hub MCP tool-name baseline drifts."""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HUB = ROOT / "hub" / "hub.py"
MANIFEST = ROOT / "docs" / "contracts" / "HUB-MCP-TOOLS-v1.md"

tree = ast.parse(HUB.read_text(encoding="utf-8"), filename=str(HUB))
build_server = next(
    node for node in tree.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and node.name == "build_server"
)

actual = set()
for node in ast.walk(build_server):
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    if any(
        isinstance(decorator, ast.Attribute)
        and decorator.attr == "tool"
        and isinstance(decorator.value, ast.Name)
        and decorator.value.id == "mcp"
        for decorator in node.decorator_list
    ):
        actual.add(node.name)

manifest_text = MANIFEST.read_text(encoding="utf-8")
documented = set(re.findall(r"^\| `([a-zA-Z_]\w*)` \|", manifest_text, re.MULTILINE))

missing = sorted(actual - documented)
stale = sorted(documented - actual)
if missing or stale:
    if missing:
        print("Missing from Hub MCP manifest:", ", ".join(missing))
    if stale:
        print("No longer registered but still documented:", ", ".join(stale))
    raise SystemExit(1)

print(f"Hub MCP tool manifest is synchronized: {len(actual)} tools")
