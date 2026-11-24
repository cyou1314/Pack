"""SQLite connection and audit-write primitives for Pack Hub."""
from __future__ import annotations

import sqlite3
import time
from os import PathLike

from .task_refs import audit_task_id


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
    # Foreign-key enforcement is connection-local in SQLite and must be enabled
    # before any transaction begins. Existing Hub tables remain unchanged; the
    # additive Agent Registry uses these constraints for new relationships.
    connection.execute("PRAGMA foreign_keys=ON")
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
        "INSERT INTO audit(ts,actor,action,detail,task_id) VALUES(?,?,?,?,?)",
        (timestamp, actor, action, detail, audit_task_id(detail)),
    )
