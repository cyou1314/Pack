"""Strict task-reference parsing shared by Hub writes and migrations."""
from __future__ import annotations

import re


SQLITE_INT_MAX = (1 << 63) - 1
AUDIT_TASK_RE = re.compile(r"^task ([0-9]+)(?:\\Z|[: ])")


def bounded_task_id(match) -> int | None:
    """Return a captured decimal ID only when SQLite can bind it as INTEGER."""
    if match is None:
        return None
    digits = match.group(1)
    if not digits or not digits.isascii() or not digits.isdigit():
        return None
    normalized = digits.lstrip("0") or "0"
    if len(normalized) > 19:
        return None
    value = int(normalized)
    return value if value <= SQLITE_INT_MAX else None


def audit_task_id(detail: str | None) -> int | None:
    """Extract only canonical task N audit prefixes.

    This deliberately preserves the old task-audit prefix semantics: leading
    zeroes, task-like suffixes (for example task 1x), and out-of-range SQLite
    integers are not associated with a task.
    """
    if not isinstance(detail, str):
        return None
    match = AUDIT_TASK_RE.match(detail)
    value = bounded_task_id(match)
    if value is None or match.group(1) != str(value):
        return None
    return value
