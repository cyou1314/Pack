"""Test-only dependency adapter for the current main-based Hub.

No live routes or production context are configured here.
"""
import sqlite3
from dataclasses import dataclass

from registry_v1_port import apply_registry_v1
from registry_v2_v3_port import apply_registry_v2_v3
from registry_event_v4_v5_port import apply_event_v4_v5


@dataclass
class HubContext:
    base_dir: str
    db_path: str
    attachment_dir: str
    lock: object
    clock: object

    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def audit(self, conn, actor, action, detail=""):
        conn.execute(
            "INSERT INTO audit(ts,actor,action,detail) VALUES(?,?,?,?)",
            (float(self.clock()), actor, action, detail),
        )

    def initialize(self, hub):
        previous = (hub.BASE, hub.DB_PATH, hub.ATTACH_DIR)
        try:
            hub.BASE, hub.DB_PATH, hub.ATTACH_DIR = (
                self.base_dir, self.db_path, self.attachment_dir)
            hub.init()
        finally:
            hub.BASE, hub.DB_PATH, hub.ATTACH_DIR = previous
        conn = self.connect()
        try:
            now = float(self.clock())
            apply_registry_v1(conn, now=now)
            apply_registry_v2_v3(conn, now=now)
            apply_event_v4_v5(conn, now=now)
        finally:
            conn.close()
