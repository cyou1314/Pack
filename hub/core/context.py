"""Explicit dependency bundle for one Pack Hub runtime instance."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .storage import connect, write_audit


@dataclass(slots=True)
class HubContext:
    """Paths and shared runtime dependencies used by Hub services.

    The default Hub keeps compatibility wrappers around one default context.
    Tests and later domain modules can construct isolated contexts without
    mutating module-level DB paths or sharing the production lock.
    """

    base_dir: str
    db_path: str
    attachment_dir: str
    lock: Any
    clock: Callable[[], float]

    def connect(self):
        """Open a database connection using this context's database path."""
        return connect(self.db_path)

    def audit(self, connection, actor: str, action: str, detail: str = "") -> None:
        """Write an audit row using this context's wall clock."""
        write_audit(
            connection,
            actor,
            action,
            detail,
            timestamp=self.clock(),
        )
