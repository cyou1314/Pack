"""Desktop lock services with cross-process single-holder enforcement."""
from __future__ import annotations

import json


def acquire_desktop(context, actor: dict, task_id: int, claim_token: str, *,
                    current_holder, snapshot) -> dict:
    """Acquire the single GUI desktop lease for a currently held task."""
    if actor["role"] != "member":
        raise ValueError("desktop lock is for members holding a task")

    with context.lock:
        conn = context.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            current_holder(conn, actor, task_id, claim_token)
            state = snapshot(conn)
            if state["held"]:
                if state["task_id"] == task_id and state["holder"] == actor["name"]:
                    return {
                        "held": True, "task_id": task_id,
                        "acquired_at": state["since"], "idempotent": True,
                    }
                raise ValueError(json.dumps({
                    "error": "desktop_busy", "holder": state["holder"],
                    "task_id": state["task_id"], "since": state["since"],
                }, ensure_ascii=False))

            now = context.now()
            conn.execute(
                "INSERT INTO desktop_lock(id,holder,task_id,acquired_at) "
                "VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "holder=excluded.holder,task_id=excluded.task_id,"
                "acquired_at=excluded.acquired_at",
                (actor["name"], task_id, now),
            )
            context.audit(
                conn, actor["name"], "desktop_acquire", f"task {task_id}",
            )
            conn.commit()
            return {"held": True, "task_id": task_id, "acquired_at": now}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def release_desktop(context, actor: dict, task_id: int = 0,
                    claim_token: str = "", *, current_holder,
                    snapshot) -> dict:
    """Release the current GUI lease or let dispatcher force-release it."""
    with context.lock:
        conn = context.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            state = snapshot(conn)
            if not state["held"]:
                return {"held": False, "released": False}

            if actor["role"] == "dispatcher":
                conn.execute(
                    "UPDATE desktop_lock SET holder=NULL,task_id=NULL,"
                    "acquired_at=NULL WHERE id=1"
                )
                context.audit(
                    conn, actor["name"], "desktop_force_release",
                    f"task {state['task_id']} holder={state['holder']}",
                )
                conn.commit()
                return {
                    "held": False, "released": True,
                    "was_task_id": state["task_id"],
                }

            if not task_id or state["task_id"] != task_id:
                raise ValueError("desktop lock is held by another task")
            current_holder(conn, actor, task_id, claim_token)
            conn.execute(
                "UPDATE desktop_lock SET holder=NULL,task_id=NULL,"
                "acquired_at=NULL WHERE id=1"
            )
            context.audit(
                conn, actor["name"], "desktop_release", f"task {task_id}",
            )
            conn.commit()
            return {
                "held": False, "released": True, "was_task_id": task_id,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def desktop_status(context, actor: dict, *, snapshot) -> dict:
    """Return the shared desktop lease state to any authenticated member."""
    conn = context.connect()
    try:
        return snapshot(conn)
    finally:
        conn.close()
