#!/usr/bin/env python3
"""Hub query-index regression: migration, correctness, and SQLite query plans."""
import asyncio
import importlib.util
import json
import os
import socket
import sqlite3
import tempfile
import threading
import time

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

HERE = os.path.dirname(os.path.abspath(__file__))
HUB_PATH = os.path.join(HERE, "hub.py")
SPEC = importlib.util.spec_from_file_location("pack_hub_query_index_test", HUB_PATH)
hub = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hub)

PASSED = 0
FAILED = 0


def check(name, condition, extra=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"PASS {name}", flush=True)
    else:
        FAILED += 1
        print(f"FAIL {name} {extra}", flush=True)


def plan_uses(conn, sql, args, index_name):
    rows = conn.execute("EXPLAIN QUERY PLAN " + sql, args).fetchall()
    detail = " | ".join(str(row[3]) for row in rows)
    return index_name in detail, detail


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_port(port, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.05)
    return False


async def call_task_audit(port, token, task_id):
    transport = StreamableHttpTransport(
        f"http://127.0.0.1:{port}/mcp",
        headers={"Authorization": f"Bearer {token}"})
    async with Client(transport) as client:
        result = await client.call_tool("task_audit", {"task_id": task_id})
        if getattr(result, "isError", False):
            raise AssertionError(str(result.content))
        if getattr(result, "structured_content", None):
            return result.structured_content
        return json.loads(result.content[0].text)


def main():
    with tempfile.TemporaryDirectory(prefix="pack-hub-index-") as temp:
        db_path = os.path.join(temp, "pack.db")
        hub.BASE = temp
        hub.DB_PATH = db_path

        # Simulate the pre-staging57 production schema and historical rows.
        old = sqlite3.connect(db_path)
        old.executescript("""
            CREATE TABLE audit(
                id INTEGER PRIMARY KEY, ts REAL NOT NULL, actor TEXT NOT NULL,
                action TEXT NOT NULL, detail TEXT);
            INSERT INTO audit VALUES
                (1, 1, 'alice', 'task_create', 'task 1: legacy event'),
                (2, 2, 'bob', 'task_claim', 'task 12 legacy event'),
                (3, 3, 'alice', 'message_post', 'message 1'),
                (4, 4, 'alice', 'other', 'task 1x is not a task prefix'),
                (5, 5, 'alice', 'member_register', 'task 9223372036854775808'),
                (6, 6, 'alice', 'member_register',
                 'task ' || replace(printf('%5000s', ' '), ' ', '9'));

            CREATE TABLE messages(
                id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL,
                author_id INTEGER NOT NULL, kind TEXT NOT NULL,
                body TEXT NOT NULL, task_id INTEGER, created_at REAL NOT NULL);
            INSERT INTO messages VALUES
                (1, 1, 2, 'approval_request', '申请批准 任务#1：旧请求', NULL, 1),
                (2, 1, 2, 'approval_request', '申请批准 任务#12：请求', NULL, 2),
                (3, 1, 2, 'approval_request', '申请批准 任务#1：较新请求', NULL, 3),
                (4, 1, 1, 'decision', '批准 任务#1（旧决议）', NULL, 4);

            CREATE TABLE tasks(
                id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL,
                title TEXT NOT NULL, body TEXT NOT NULL,
                created_by INTEGER NOT NULL, assignee INTEGER, status TEXT NOT NULL,
                claim_token TEXT, lease_expires REAL,
                attempts INTEGER NOT NULL DEFAULT 0, result TEXT,
                review_note TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL);
            CREATE TABLE task_transfers(
                id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL,
                from_member TEXT NOT NULL, to_member TEXT NOT NULL,
                reason TEXT, actor TEXT NOT NULL, created_at REAL NOT NULL);
            CREATE TABLE review_log(
                id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL,
                target_url TEXT NOT NULL, owner_quote TEXT NOT NULL,
                member_desc TEXT NOT NULL, verdict TEXT NOT NULL,
                reviewer TEXT NOT NULL, created_at REAL NOT NULL);
        """)
        old.commit()
        old.close()

        hub.init()
        runtime_conn = hub.db()
        journal_mode = runtime_conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = runtime_conn.execute("PRAGMA busy_timeout").fetchone()[0]
        check("init enables persistent WAL mode", journal_mode.lower() == "wal",
              str(journal_mode))
        check("runtime DB connections retain busy timeout", busy_timeout == 30000,
              str(busy_timeout))
        runtime_conn.close()

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        task_cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
        transfer_cols = {
            r["name"] for r in conn.execute("PRAGMA table_info(task_transfers)")
        }
        check("legacy tasks table receives scheduling columns before indexes",
              {"sched_at", "urgent", "next_claim_at"} <= task_cols,
              str(sorted(task_cols)))
        check("legacy transfers table receives expiry columns before indexes",
              {"status", "accepted_at", "expires_at"} <= transfer_cols,
              str(sorted(transfer_cols)))
        task_indexes = {
            r["name"] for r in conn.execute("PRAGMA index_list(tasks)")
        }
        check("legacy tasks table receives hot-path indexes",
              {"idx_tasks_urgent_id", "idx_tasks_assignee_urgent_id",
               "idx_tasks_room_urgent_id", "idx_tasks_active_queue",
               "idx_tasks_lease_expiry", "idx_tasks_unclaimed_due"} <= task_indexes,
              str(sorted(task_indexes)))
        transfer_indexes = {
            r["name"] for r in conn.execute("PRAGMA index_list(task_transfers)")
        }
        check("legacy transfers table receives pending-expiry index",
              "idx_task_transfers_pending_expiry" in transfer_indexes,
              str(sorted(transfer_indexes)))

        audit_cols = {r["name"] for r in conn.execute("PRAGMA table_info(audit)")}
        check("legacy audit table receives task_id column", "task_id" in audit_cols)
        legacy_audit = {
            r["id"]: r["task_id"]
            for r in conn.execute("SELECT id, task_id FROM audit WHERE id <= 6")
        }
        check("legacy audit rows backfilled by exact task prefix",
              legacy_audit == {1: 1, 2: 12, 3: None, 4: None, 5: None, 6: None},
              str(legacy_audit))

        approval_rows = conn.execute(
            "SELECT id, task_id FROM messages WHERE kind='approval_request' ORDER BY id"
        ).fetchall()
        check("legacy approval_request rows receive task_id",
              [(r["id"], r["task_id"]) for r in approval_rows] == [(1, 1), (2, 12), (3, 1)],
              str([(r["id"], r["task_id"]) for r in approval_rows]))
        req1, decided1 = hub._approval_state(conn, 1)
        req12, decided12 = hub._approval_state(conn, 12)
        check("approval lookup chooses latest request for task 1",
              req1 is not None and req1["id"] == 3, str(req1["id"] if req1 else None))
        check("decision scan recognizes dispatcher decision",
              decided1 == "message", str(decided1))
        check("task 12 request does not collide with task 1 decision",
              req12 is not None and req12["id"] == 2 and decided12 is None,
              f"req={req12['id'] if req12 else None} decided={decided12}")

        dispatcher_id = conn.execute(
            "SELECT id FROM members WHERE role='dispatcher'").fetchone()["id"]
        conn.execute(
            "INSERT INTO messages(room_id,author_id,kind,body,task_id,created_at) "
            "VALUES(1,?,'chat','批准 任务#12（历史人工决议）',12,5)",
            (dispatcher_id,))
        req12, decided12 = hub._approval_state(conn, 12)
        check("approval lookup still recognizes historical chat decisions",
              req12 is not None and req12["id"] == 2 and decided12 == "message",
              str(decided12))

        # A newer NULL-task_id legacy request must still beat an older indexed request.
        conn.execute(
            "INSERT INTO messages(room_id,author_id,kind,body,task_id,created_at) "
            "VALUES(1,?,'approval_request','申请批准 任务#77：旧式直写',77,6)",
            (dispatcher_id,))
        conn.execute(
            "INSERT INTO messages(room_id,author_id,kind,body,task_id,created_at) "
            "VALUES(1,?,'approval_request','申请批准 任务#77：未回填直写',NULL,7)",
            (dispatcher_id,))
        req77, decided77 = hub._approval_state(conn, 77)
        check("newer legacy NULL request is preserved by fallback",
              req77 is not None and req77["id"] == 7 and decided77 is None,
              str(req77["id"] if req77 else None))

        # Review-log lookup remains task-scoped and indexed.
        conn.execute(
            "INSERT INTO messages(room_id,author_id,kind,body,task_id,created_at) "
            "VALUES(1,?,'approval_request','申请批准 任务#90：待审',90,8)",
            (dispatcher_id,))
        conn.execute(
            "INSERT INTO review_log(task_id,target_url,owner_quote,member_desc,verdict,reviewer,created_at) "
            "VALUES(90,'https://example.com','quote','desc','allow','reviewer',9)")
        req90, decided90 = hub._approval_state(conn, 90)
        check("review_log decision lookup preserved", req90 is not None and decided90 == "review",
              str(decided90))

        hub.audit(conn, "alice", "task_claim", "task 1")
        hub.audit(conn, "bob", "task_transfer", "task 12: to bob")
        hub.audit(conn, "alice", "message_post", "message 1")
        hub.audit(conn, "alice", "member_register", "task 9223372036854775808")
        overflow = conn.execute(
            "SELECT task_id, detail FROM audit WHERE action='member_register' "
            "AND detail='task 9223372036854775808'").fetchone()
        check("new out-of-range task-like detail remains safely unassociated",
              overflow is not None and overflow["task_id"] is None)
        events1, trunc1 = hub._task_audit_events(conn, 1)
        events12, trunc12 = hub._task_audit_events(conn, 12)
        check("new task audit writes populate task_id",
              events1[-1]["action"] == "task_claim" and events12[-1]["action"] == "task_transfer")
        check("task audit structured filter prevents 1/12 collision",
              [e["detail"] for e in events1] == ["task 1: legacy event", "task 1"])
        check("task audit does not include unrelated message events",
              all(e["action"] != "message_post" for e in events1))
        check("task audit initial event stream is not truncated",
              trunc1 is False and trunc12 is False)

        for i in range(201):
            hub.audit(conn, "load", "load_probe", "task 999: event")
        events999, trunc999 = hub._task_audit_events(conn, 999)
        check("task audit keeps 200-row cap and reports truncation",
              len(events999) == 200 and trunc999 is True,
              f"rows={len(events999)} truncated={trunc999}")

        # Seed a representative queue so SQLite can cost the same access paths
        # used by task_list and the minute-level sweeper.
        now = time.time()
        member_ids = []
        member_names = []
        for i in range(8):
            name = f"query-index-member-{i}"
            cur = conn.execute(
                "INSERT INTO members(name,role,token_hash,created_at) VALUES(?,?,?,?)",
                (name, "member", hub.hash_token(f"query-index-token-{i}"), now))
            member_ids.append(cur.lastrowid)
            member_names.append(name)
        room_ids = []
        room_names = []
        for i in range(8):
            name = f"query-index-room-{i}"
            cur = conn.execute(
                "INSERT INTO rooms(name,created_at) VALUES(?,?)", (name, now))
            room_ids.append(cur.lastrowid)
            room_names.append(name)
        statuses = ("open", "claimed", "working", "review", "done", "blocked")
        task_rows = []
        for i in range(2000):
            status = statuses[i % len(statuses)]
            assignee = member_ids[i % len(member_ids)] if i % 5 else None
            lease_expires = now - (i % 600) if status in ("claimed", "working") else None
            next_claim_at = (None if i % 3 == 0 else
                             now - 60 if i % 3 == 1 else now + 60)
            task_rows.append((
                room_ids[i % len(room_ids)], f"query task {i}", "index fixture",
                dispatcher_id, assignee, status, lease_expires, i % 3,
                next_claim_at, now, now, i % 2))
        conn.executemany(
            """INSERT INTO tasks(room_id,title,body,created_by,assignee,status,
                                 lease_expires,attempts,next_claim_at,created_at,
                                 updated_at,urgent)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            task_rows)
        transfer_rows = [
            (i + 1, "from-member", "to-member", "fixture", now,
             "pending" if i % 2 else "accepted", now - 30)
            for i in range(300)
        ]
        conn.executemany(
            """INSERT INTO task_transfers(task_id,from_member,to_member,reason,
                                          created_at,status,expires_at)
               VALUES(?,?,?,?,?,?,?)""",
            transfer_rows)
        conn.commit()
        conn.execute("ANALYZE")

        task_select = """SELECT t.id, t.title, t.body, t.status,
                                mb.name AS assignee, t.attempts, t.result,
                                t.envelope, t.lease_expires, t.updated_at,
                                t.fail_class, t.next_claim_at, t.sched_at, t.urgent,
                                cr.name AS created_by, t.review_note, rm.name AS room
                         FROM tasks t LEFT JOIN members mb ON mb.id=t.assignee
                         LEFT JOIN members cr ON cr.id=t.created_by
                         JOIN rooms rm ON rm.id=t.room_id"""
        plans = [
            ("task list default ordering",
             task_select + " WHERE 1=1 ORDER BY t.urgent DESC,t.id ASC LIMIT 100 OFFSET ?",
             (0,), "idx_tasks_urgent_id"),
            ("task list assignee filter",
             task_select + " WHERE mb.name=? ORDER BY t.urgent DESC,t.id ASC LIMIT 100 OFFSET ?",
             (member_names[0], 0), "idx_tasks_assignee_urgent_id"),
            ("task list room filter",
             task_select + " WHERE rm.name=? ORDER BY t.urgent DESC,t.id ASC LIMIT 100 OFFSET ?",
             (room_names[0], 0), "idx_tasks_room_urgent_id"),
            ("active queue rank scan",
             """SELECT id,assignee FROM tasks
                WHERE status IN ('open','claimed','working')
                ORDER BY urgent DESC,id ASC""",
             (), "idx_tasks_active_queue"),
            ("lease expiry sweeper",
             """SELECT * FROM tasks
                WHERE status IN ('claimed','working')
                  AND lease_expires IS NOT NULL AND lease_expires<?""",
             (now,), "idx_tasks_lease_expiry"),
            ("unclaimed assigned-task sweeper",
             """SELECT t.*, m.name AS a_name, m.last_heartbeat AS a_hb,
                       m.quota_week_used_pct AS a_pct,
                       m.quota_updated_at AS a_quota_at, m.kind AS a_kind
                FROM tasks t JOIN members m ON m.id=t.assignee
                WHERE t.status='open' AND t.assignee IS NOT NULL
                  AND m.removed_at IS NULL
                  AND (t.next_claim_at IS NULL OR t.next_claim_at<=?)""",
             (now,), "idx_tasks_unclaimed_due"),
            ("pending transfer expiry sweeper",
             """SELECT * FROM task_transfers
                WHERE status='pending' AND expires_at IS NOT NULL AND expires_at<?""",
             (now,), "idx_task_transfers_pending_expiry"),
            ("unclaimed escalation dedupe",
             """SELECT 1 FROM audit WHERE task_id=?
                AND action='task_unclaimed_escalate' AND detail LIKE ? LIMIT 1""",
             (1, "task 1 assignee_id=2 %"), "idx_audit_task"),
        ]
        for name, sql, args, index in plans:
            used, detail = plan_uses(conn, sql, args, index)
            if name == "active queue rank scan":
                used = used and "USE TEMP B-TREE FOR ORDER BY" not in detail
            check(f"query plan uses {index}", used, f"{name}: {detail}")

            ("approval request lookup",
             """SELECT * FROM messages WHERE kind='approval_request' AND task_id=?
                ORDER BY id DESC LIMIT 1""", (1,), "idx_messages_approval_task"),
            ("legacy approval fallback",
             """SELECT * FROM messages WHERE kind='approval_request' AND task_id IS NULL
                ORDER BY id DESC""", (), "idx_messages_approval_task"),
            ("dispatcher decision lookup",
             """SELECT body FROM messages WHERE id>? AND kind IN ('chat','decision')
                AND author_id=? ORDER BY id""", (1, dispatcher_id),
             "idx_messages_dispatch_decisions"),
            ("review log lookup",
             "SELECT id FROM review_log WHERE task_id=? AND created_at>=?", (90, 0),
             "idx_review_log_task_time"),
            ("task audit lookup",
             """SELECT ts,actor,action,detail FROM audit
                WHERE task_id=? ORDER BY id ASC LIMIT 201""", (1,), "idx_audit_task"),
        ]
        for name, sql, args, index in plans:
            used, detail = plan_uses(conn, sql, args, index)
            check(f"query plan uses {index}", used, f"{name}: {detail}")

        conn.commit()
        conn.close()

        hub.init()
        conn = sqlite3.connect(db_path)
        audit_count = conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
        approval_count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE kind='approval_request'").fetchone()[0]
        check("init rerun is idempotent", audit_count == 212 and approval_count == 6,
              f"audit={audit_count} approvals={approval_count}")
        conn.close()

        # Exercise the registered MCP task_audit tool, not only its SQL helper.
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        room_id = conn.execute(
            "SELECT id FROM rooms WHERE name='pack-main'").fetchone()["id"]
        dispatcher_id = conn.execute(
            "SELECT id FROM members WHERE role='dispatcher'").fetchone()["id"]
        now = time.time()
        cur = conn.execute(
            """INSERT INTO tasks(room_id,title,body,created_by,assignee,status,
                                 created_at,updated_at)
               VALUES(?,?,?,?,NULL,'open',?,?)""",
            (room_id, "query-index integration", "test", dispatcher_id, now, now))
        integration_task_id = cur.lastrowid
        hub.audit(conn, "dispatcher", "task_create",
                  f"task {integration_task_id}: actual MCP tool path")
        conn.commit()
        conn.close()

        port = free_port()
        server = hub.build_server()
        thread = threading.Thread(
            target=server.run,
            kwargs={"transport": "http", "host": "127.0.0.1",
                    "port": port, "path": "/mcp"},
            daemon=True)
        thread.start()
        server_ready = wait_port(port)
        check("MCP test server starts", server_ready, f"port={port}")
        if server_ready:
            token = open(os.path.join(temp, ".dispatcher_token")).read().strip()
            result = asyncio.run(call_task_audit(port, token, integration_task_id))
            check("registered task_audit tool returns indexed event",
                  result["task"]["id"] == integration_task_id
                  and any(e["detail"] == f"task {integration_task_id}: actual MCP tool path"
                          for e in result["events"]),
                  str(result.get("events", [])[-3:]))
            check("registered task_audit preserves truncation field",
                  result["events_truncated"] is False)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
