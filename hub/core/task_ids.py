"""Strict task-ID parsing shared by audit and approval paths."""
from __future__ import annotations

import re

AUDIT_TASK_RE = re.compile(r"^task ([0-9]+)(?:$|[: ])")
SQLITE_INT_MAX = (1 << 63) - 1


def bounded_task_id(match) -> int | None:
    """Convert a captured decimal task ID only when it fits SQLite INTEGER."""
    if not match:
        return None
    digits = match.group(1).lstrip("0") or "0"
    if len(digits) > 19:
        return None
    value = int(digits)
    return value if value <= SQLITE_INT_MAX else None


def audit_task_id(detail: str | None) -> int | None:
    """Extract the exact legacy 'task N' prefix without accepting lookalikes."""
    if not isinstance(detail, str):
        return None
    return bounded_task_id(AUDIT_TASK_RE.match(detail))
