#!/usr/bin/env python3
"""Task lifecycle compare-and-swap regressions through the real MCP tool surface."""
import asyncio
import base64
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
SPEC = importlib.util.spec_from_file_location(
    "pack_hub_lifecycle_guard_test", os.path.join(HERE, "hub.py"))
hub = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hub)


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


async def call(port, token, name, args):
    transport = StreamableHttpTransport(
        f"http://127.0.0.1:{port}/mcp",
        headers={"Authorization": f"Bearer {token}"})
    try:
        async with Client(transport) as client:
            result = await client.call_tool(name, args)
            if getattr(result, "isError", False):
                return {"error": result.content[0].text if result.content else "tool_error"}
            structured = getattr(result, "structured_content", None)
            if structured is not None:
                return structured
            return json.loads(result.content[0].text)
    except Exception as exc:
        return {"error": str(exc)}


async def run_checks(port, dispatcher_token, a_token, b_token):
    dispatcher = lambda name, args: call(port, dispatcher_token, name, args)
    member_a = lambda name, args: call(port, a_token, name, args)
    member_b = lambda name, args: call(port, b_token, name, args)

    # Accepted transfer followed by cancellation must invalidate the receiver's
    # token and leave a consistent terminal task row.
    created = await dispatcher("task_create", {
        "room": "pack-main", "title": "transfer then cancel",
        "body": "guard test", "assignee_name": "guard-member-a"})
    task_id = created["task_id"]
    claimed = await member_a("task_claim", {"task_id": task_id})
    transfer = await member_a("task_transfer", {
        "task_id": task_id, "claim_token": claimed["claim_token"],
        "to_member": "guard-member-b", "reason": "guard test"})
    accepted = await member_b("task_accept_transfer", {"task_id": task_id})
    assert accepted.get("claim_token"), accepted
    cancelled = await dispatcher("task_cancel", {"task_id": task_id})
    assert cancelled["status"] == "cancelled", cancelled
    stale_update = await member_b("task_update", {
        "task_id": task_id, "claim_token": accepted["claim_token"],
        "note": "must not revive cancelled task"})
    assert "error" in stale_update, stale_update
    stale_complete = await member_b("task_complete", {
        "task_id": task_id, "claim_token": accepted["claim_token"],
        "result": "must not complete cancelled task"})
    assert "error" in stale_complete, stale_complete

    # Cancellation while a transfer is still pending must prevent the receiver
    # from acquiring a claim token.
    created2 = await dispatcher("task_create", {
        "room": "pack-main", "title": "pending transfer then cancel",
        "body": "guard test", "assignee_name": "guard-member-a"})
    task2 = created2["task_id"]
    claimed2 = await member_a("task_claim", {"task_id": task2})
    pending = await member_a("task_transfer", {
        "task_id": task2, "claim_token": claimed2["claim_token"],
        "to_member": "guard-member-b", "reason": "cancel before accept"})
    cancelled2 = await dispatcher("task_cancel", {"task_id": task2})
    assert cancelled2["status"] == "cancelled", cancelled2
    rejected_accept = await member_b("task_accept_transfer", {"task_id": task2})
    assert "error" in rejected_accept, rejected_accept

    # Two concurrent review decisions are a single-winner state transition.
    created3 = await dispatcher("task_create", {
        "room": "pack-main", "title": "concurrent review",
        "body": "guard test", "assignee_name": "guard-member-a"})
    task3 = created3["task_id"]
    claimed3 = await member_a("task_claim", {"task_id": task3})
    completed3 = await member_a("task_complete", {
        "task_id": task3, "claim_token": claimed3["claim_token"],
        "result": "ready for review"})
    assert completed3["status"] == "review", completed3
    decisions = await asyncio.gather(
        dispatcher("task_review", {"task_id": task3, "approve": True, "note": "approve"}),
        dispatcher("task_review", {"task_id": task3, "approve": False, "note": "reject"}),
    )
    winners = [x for x in decisions if "error" not in x]
    losers = [x for x in decisions if "error" in x]
    assert len(winners) == 1 and len(losers) == 1, decisions

    # Content-addressed attachments are verified on read and repaired on
    # re-upload if the stored blob has been corrupted.
    payload = b"\x89PNG\r\n\x1a\npack-attachment-test"
    encoded = base64.b64encode(payload).decode("ascii")
    uploaded = await member_a("attachment_put", {
        "room": "pack-main", "filename": "guard.png", "mime": "image/png",
        "data_base64": encoded})
    attachment_id = uploaded["attachment_id"]
    fetched = await member_a("attachment_get", {"attachment_id": attachment_id})
    assert fetched["data_base64"] == encoded, fetched
    blob = hub._attach_path(uploaded["sha256"], "image/png")
    with open(blob, "wb") as stream:
        stream.write(b"corrupted")
    corrupted = await member_a("attachment_get", {"attachment_id": attachment_id})
    assert "error" in corrupted and "integrity" in corrupted["error"], corrupted
    repaired = await member_a("attachment_put", {
        "room": "pack-main", "filename": "guard.png", "mime": "image/png",
        "data_base64": encoded})
    repaired_result = await member_a("attachment_get", {
        "attachment_id": repaired["attachment_id"]})
    assert repaired_result["data_base64"] == encoded, repaired_result
    assert os.stat(blob).st_mode & 0o777 == 0o600
    assert os.stat(hub.ATTACH_DIR).st_mode & 0o777 == 0o700

    return {"accepted_transfer": transfer["transfer_id"],
            "cancelled_pending_transfer": pending["transfer_id"],
            "review_winner": winners[0]["status"]}


def main():
    with tempfile.TemporaryDirectory(prefix="pack-hub-lifecycle-") as temp:
        hub.BASE = temp
        hub.DB_PATH = os.path.join(temp, "pack.db")
        hub.ATTACH_DIR = os.path.join(temp, "attachments")
        hub.init()
        dispatcher_token = open(os.path.join(temp, ".dispatcher_token"), encoding="utf-8").read().strip()

        port = free_port()
        server = hub.build_server()
        thread = threading.Thread(
            target=server.run,
            kwargs={"transport": "http", "host": "127.0.0.1",
                    "port": port, "path": "/mcp"},
            daemon=True)
        thread.start()
        if not wait_port(port):
            raise AssertionError(f"MCP test server did not start on {port}")

        async def register(name):
            return await call(port, dispatcher_token, "member_register", {"name": name})
        a = asyncio.run(register("guard-member-a"))
        b = asyncio.run(register("guard-member-b"))
        result = asyncio.run(run_checks(port, dispatcher_token, a["token"], b["token"]))

        conn = sqlite3.connect(hub.DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id,status,claim_token FROM tasks WHERE title IN "
            "('transfer then cancel','pending transfer then cancel') ORDER BY id").fetchall()
        assert len(rows) == 2 and all(r["status"] == "cancelled" and r["claim_token"] is None for r in rows), [
            dict(r) for r in rows]
        transfer_states = conn.execute(
            "SELECT task_id,status FROM task_transfers ORDER BY id").fetchall()
        assert [r["status"] for r in transfer_states] == ["accepted", "pending"], [
            dict(r) for r in transfer_states]

        # Automatic relay is a savepoint: a failure after the guarded task
        # update must roll back the assignment together with the transfer row.
        now = time.time()
        member_a_id = conn.execute(
            "SELECT id FROM members WHERE name='guard-member-a'").fetchone()["id"]
        member_b_id = conn.execute(
            "SELECT id FROM members WHERE name='guard-member-b'").fetchone()["id"]
        dispatcher_id = conn.execute(
            "SELECT id FROM members WHERE role='dispatcher'").fetchone()["id"]
        room_id = conn.execute(
            "SELECT id FROM rooms WHERE name='pack-main'").fetchone()["id"]
        conn.execute(
            "UPDATE members SET last_heartbeat=?, quota_updated_at=?, "
            "quota_week_used_pct=10 WHERE id=?",
            (now, now, member_b_id))
        cur = conn.execute(
            """INSERT INTO tasks(room_id,title,body,created_by,assignee,status,
                                 claim_token,lease_expires,attempts,created_at,updated_at)
               VALUES(?,?,?,?,?,'blocked',NULL,NULL,1,?,?)""",
            (room_id, "auto relay savepoint", "guard test", dispatcher_id,
             member_a_id, now, now))
        relay_task_id = cur.lastrowid
        relay_task = conn.execute(
            "SELECT * FROM tasks WHERE id=?", (relay_task_id,)).fetchone()
        conn.execute(
            """CREATE TRIGGER fail_auto_relay_insert BEFORE INSERT ON task_transfers
               WHEN NEW.actor='auto-relay'
               BEGIN SELECT RAISE(ABORT, 'injected relay failure'); END""")
        conn.commit()
        try:
            with hub._lock:
                hub._auto_relay(conn, relay_task, "guard-member-a", "test")
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("injected auto-relay failure was not raised")
        after_failure = conn.execute(
            "SELECT status,assignee,claim_token FROM tasks WHERE id=?",
            (relay_task_id,)).fetchone()
        assert after_failure["status"] == "blocked"
        assert after_failure["assignee"] == member_a_id
        assert after_failure["claim_token"] is None
        assert conn.execute(
            "SELECT COUNT(*) FROM task_transfers WHERE task_id=?",
            (relay_task_id,)).fetchone()[0] == 0
        conn.execute("DROP TRIGGER fail_auto_relay_insert")
        conn.commit()
        relay_task = conn.execute(
            "SELECT * FROM tasks WHERE id=?", (relay_task_id,)).fetchone()
        with hub._lock:
            relayed_to = hub._auto_relay(conn, relay_task, "guard-member-a", "test")
        assert relayed_to == "guard-member-b", relayed_to
        conn.commit()
        after_success = conn.execute(
            "SELECT status,assignee,claim_token FROM tasks WHERE id=?",
            (relay_task_id,)).fetchone()
        assert after_success["status"] == "claimed"
        assert after_success["assignee"] == member_b_id
        assert after_success["claim_token"] is None
        assert conn.execute(
            "SELECT COUNT(*) FROM task_transfers WHERE task_id=? AND status='pending'",
            (relay_task_id,)).fetchone()[0] == 1
        conn.close()
        print("PASS task lifecycle compare-and-swap guards and auto-relay savepoint", result)


if __name__ == "__main__":
    main()
