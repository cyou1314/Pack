"""SQLite connection and audit-write primitives for Pack Hub."""
from __future__ import annotations

import sqlite3
import time
from os import PathLike


def connect(
    db_path: str | PathLike[str],
    *,
    timeout: float = 30,
    busy_timeout_ms: int = 30000,
) -> sqlite3.Connection:
    """Open a configured Hub SQLite connection.

    Connection lifecycle and transaction ownership remain with the caller.
    """
    connection = sqlite3.connect(str(db_path), timeout=timeout)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    return connection


def write_audit(
    connection: sqlite3.Connection,
    actor: str,
    action: str,
    detail: str = "",
    *,
    timestamp: float | None = None,
) -> None:
    """Insert one audit row without committing the caller's transaction."""
    if timestamp is None:
        timestamp = time.time()
    connection.execute(
        "INSERT INTO audit(ts,actor,action,detail) VALUES(?,?,?,?)",
        (timestamp, actor, action, detail),
    )
