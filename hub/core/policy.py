"""Pure policy primitives shared by the Pack Hub service.

This module deliberately has no database, filesystem, network, or MCP dependency.
Keep side-effecting enforcement (audit writes, DB lookups, tool registration) in
hub.py until those responsibilities have explicit service contexts.
"""
import re

ACTION_TAG_RE = re.compile(r"\[([A-Za-z0-9_-]+)\]")
ACCESS_MODES = ("full", "workspace", "readonly")
BACKOFF_BASE_SECONDS = {
    "quota": 1800,
    "transient": 60,
    "task": 300,
    "unknown": 300,
}
EXECUTOR_MARKERS = ("[M4A]", "[ENG]", "[ENG-image]")


def action_tag(title):
    """Return the first supported bracket marker in a task title."""
    match = ACTION_TAG_RE.search(title or "")
    return f"[{match.group(1)}]" if match else ""


def backoff_seconds(fail_class, attempts):
    """Return exponential backoff for a failure class."""
    base = BACKOFF_BASE_SECONDS.get(fail_class, BACKOFF_BASE_SECONDS["unknown"])
    return base * (2 ** max(0, attempts - 1))


def is_executor_pinned(title, body):
    """Whether title/body marks a task as bound to a specific local executor."""
    normalized_title = (title or "").lstrip()
    if any(normalized_title.startswith(marker) for marker in EXECUTOR_MARKERS):
        return True
    normalized_body = body or ""
    return any(marker in normalized_body for marker in EXECUTOR_MARKERS)


def member_access_mode(member):
    """Normalize legacy, missing, or invalid member modes to full access."""
    mode = (member or {}).get("access_mode") or "full"
    return mode if mode in ACCESS_MODES else "full"
