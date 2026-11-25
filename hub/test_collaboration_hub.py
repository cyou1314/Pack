#!/usr/bin/env python3
"""Exercise opt-in collaboration cancellation against the actual Hub schema and MCP."""
import asyncio
import os
import tempfile
import threading
import time

from test_task_lifecycle_guards import hub, free_port, wait_port, call
from collaboration_store import initialize, write_transaction, create_run, snapshot
from collaboration_dispatch import bind_task, reserve_claim
from collaboration_guard import install_task_guards


def fixture(conn, room, owner, member, source, now, *, deadline, claimed=False, lease=None,
            create_card=True):
    with write_transaction(conn):
        run = create_run(conn, room_id=room, source_message_id=source, deadline=deadline,
                         now=now, actor_role="owner", mode="focus",
                         eligible={str(member)}, leader=str(member))
        if not create_card:
            return run, None
        task = conn.execute(
            "INSERT INTO tasks(room_id,title,body,created_by,assignee,status,created_at,updated_at)"
            " VALUES(?,?,?,?,?,'open',?,?)",
            (room, "collaboration fixture", "test input", owner, member, now, now)).lastrowid
        bind_task(conn, run, "answer", task_id=task, member_id=member, now=now)
        if claimed:
            assert reserve_claim(conn, task, member_id=member, eligible_ids={member}, now=now)
            conn.execute(
                "UPDATE tasks SET status='claimed',attempts=1,claim_token='fixture',lease_expires=? WHERE id=?",
                (lease, task))
            conn.execute(
                "INSERT INTO runs(task_id,member_id,fence,claim_token,status,started_at)"
                " VALUES(?,?,1,'fixture','running',?)", (task, member, now))
        return run, task


def assert_closed(conn, run, task, status):
    assert snapshot(conn, run)["status"] == status
    if task is not None:
        row = conn.execute("SELECT status,claim_token,lease_expires FROM tasks WHERE id=?",
                           (task,)).fetchone()
        assert tuple(row) == ("cancelled", None, None), tuple(row)
        assert not conn.execute(
            "SELECT 1 FROM runs WHERE task_id=? AND status='running'", (task,)).fetchone()


def main():
    with tempfile.TemporaryDirectory(prefix="pack-collab-hub-") as temp:
        hub.BASE = temp
        hub.DB_PATH = os.path.join(temp, "pack.db")
        hub.ATTACH_DIR = os.path.join(temp, "attachments")
        hub.init()
        with open(os.path.join(temp, ".dispatcher_token"), encoding="utf-8") as f:
            token = f.read().strip()
        conn = hub.db()
        try:
            assert not hub._collaboration_present(conn), "legacy schema must stay unchanged"
            initialize(conn)
            install_task_guards(conn)
            port = free_port()
            server = hub.build_server()
            threading.Thread(target=server.run, kwargs={
                "transport": "http", "host": "127.0.0.1", "port": port, "path": "/mcp"
            }, daemon=True).start()
            assert wait_port(port)

            def dispatch(name, args):
                return asyncio.run(call(port, token, name, args))

            reg = dispatch("member_register", {"name": "collab-fixture-member"})
            assert reg.get("token"), reg
            member = conn.execute("SELECT id FROM members WHERE name='collab-fixture-member'").fetchone()[0]
            owner = conn.execute("SELECT id FROM members WHERE role='dispatcher'").fetchone()[0]
            room = conn.execute("SELECT id FROM rooms WHERE name='pack-main'").fetchone()[0]
            now = time.time()
            run, task = fixture(conn, room, owner, member, 1, now,
                                deadline=now+600, claimed=True, lease=now+300)
            conn.execute("UPDATE desktop_lock SET holder=?,task_id=?,acquired_at=? WHERE id=1",
                         ("collab-fixture-member", task, now))
            conn.commit()
            result = dispatch("task_cancel", {"task_id": task})
            assert result.get("status") == "cancelled", result
            assert_closed(conn, run, task, "cancelled")
            assert not hub._desktop_snapshot(conn)["held"]
            assert dispatch("task_cancel", {"task_id": task}).get("status") == "cancelled"

            expired, expired_task = fixture(conn, room, owner, member, 2, now-100,
                                            deadline=now-10)
            failed, failed_task = fixture(conn, room, owner, member, 3, now-100,
                                          deadline=now+600, claimed=True, lease=now-10)
            legacy = dispatch("task_create", {"room": "pack-main", "title": "legacy",
                              "body": "test", "assignee_name": "collab-fixture-member"})
            legacy_id = legacy["task_id"]
            hub.sweep_once()
            assert_closed(conn, expired, expired_task, "expired")
            assert_closed(conn, failed, failed_task, "failed")
            assert conn.execute("SELECT status FROM tasks WHERE id=?", (legacy_id,)).fetchone()[0] == "open"

            removed, removed_task = fixture(conn, room, owner, member, 4, now, deadline=now+600)
            unbound, _ = fixture(conn, room, owner, member, 5, now, deadline=now+600, create_card=False)
            result = dispatch("member_remove", {"name": "collab-fixture-member"})
            assert result.get("removed") == "collab-fixture-member", result
            assert_closed(conn, removed, removed_task, "failed")
            assert_closed(conn, unbound, None, "failed")
            assert tuple(conn.execute("SELECT status,assignee FROM tasks WHERE id=?",
                                      (legacy_id,)).fetchone()) == ("open", None)
            print("PASS real Hub collaboration cancellation, expiry, member removal and legacy compatibility")
        finally:
            conn.close()


if __name__ == "__main__":
    main()
