"""Pure policy primitives shared by the Pack Hub service.

This module deliberately has no database, filesystem, network, or MCP dependency.
Keep side-effecting enforcement (audit writes, DB lookups, tool registration) in
hub.py until those responsibilities have explicit service contexts.
"""
import re

import urllib.parse

_HOST_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
_HOSTISH_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+")

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


def egress_host(raw):
    """Normalize an HTTP(S) URL to a lowercase/IDNA hostname."""
    url = (raw or "").strip()
    if not url:
        raise ValueError("malformed url: empty")
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError as error:
        raise ValueError(f"malformed url: {error}")
    if parts.scheme.lower() not in ("http", "https"):
        raise ValueError("malformed url: scheme must be http or https")
    host = parts.hostname
    if not host:
        raise ValueError("malformed url: no host")
    host = host.rstrip(".")
    if not host or ".." in host or any(char in host for char in " \t/\\"):
        raise ValueError(f"malformed host: {host!r}")
    if not host.isascii():
        try:
            host = host.encode("idna").decode("ascii")
        except (UnicodeError, ValueError):
            raise ValueError(f"malformed host (idna): {host!r}")
    return host


def egress_pattern(raw):
    """Normalize a domain rule, accepting an optional '*.' prefix."""
    pattern = (raw or "").strip().lower()
    if pattern.startswith("*."):
        pattern = pattern[2:]
    pattern = pattern.rstrip(".")
    labels = pattern.split(".") if pattern else []
    if len(labels) < 2 or any(not _HOST_LABEL_RE.fullmatch(label) for label in labels):
        raise ValueError(
            f"malformed egress pattern: {raw!r} (want a domain like example.com)"
        )
    return pattern


def host_covers(host, pattern):
    """Match an exact host or a subdomain at a label boundary."""
    return host == pattern or host.endswith("." + pattern)


def task_hosts(text):
    """Extract candidate fully-qualified hostnames from task text."""
    return {
        match.group(0).rstrip(".")
        for match in _HOSTISH_RE.finditer((text or "").lower())
    }

