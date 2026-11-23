"""Task template CRUD and firing services."""
from __future__ import annotations


def upsert_template(context, actor: dict, name: str, title_tpl: str,
                    body_tpl: str, assignee: str = "") -> dict:
    """Create or update one task template with metadata audit."""
    if not name or len(name) > 40:
        raise ValueError("template name must be 1-40 chars")
    if not title_tpl or len(title_tpl) > 200:
        raise ValueError("title_tpl must be 1-200 chars")
    if len(body_tpl) > 4000:
        raise ValueError(f"body_tpl too long: {len(body_tpl)} > 4000")

    with context.lock:
        conn = context.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if assignee:
                member = conn.execute(
                    "SELECT id,removed_at FROM members WHERE name=?",
                    (assignee,),
                ).fetchone()
                if not member:
                    raise ValueError(f"no such member: {assignee}")
                if member["removed_at"] is not None:
                    raise ValueError(f"member is removed: {assignee}")
            now = context.now()
            conn.execute(
                "INSERT INTO task_templates(name,title_tpl,body_tpl,assignee,"
                "updated_at,updated_by) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "title_tpl=excluded.title_tpl,body_tpl=excluded.body_tpl,"
                "assignee=excluded.assignee,updated_at=excluded.updated_at,"
                "updated_by=excluded.updated_by",
                (name, title_tpl, body_tpl, assignee, now, actor["name"]),
            )
            context.audit(
                conn, actor["name"], "template_upsert", f"template: {name}",
            )
            row = conn.execute(
                "SELECT id FROM task_templates WHERE name=?", (name,),
            ).fetchone()
            conn.commit()
            return {"template_id": row["id"], "name": name}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def list_templates(context) -> dict:
    conn = context.connect()
    try:
        rows = conn.execute(
            "SELECT id,name,title_tpl,body_tpl,assignee,updated_at,updated_by "
            "FROM task_templates ORDER BY id"
        ).fetchall()
        return {"templates": [dict(row) for row in rows]}
    finally:
        conn.close()


def delete_template(context, actor: dict, name: str) -> dict:
    with context.lock:
        conn = context.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "DELETE FROM task_templates WHERE name=?", (name,),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"no such template: {name}")
            context.audit(
                conn, actor["name"], "template_delete", f"template: {name}",
            )
            conn.commit()
            return {"deleted": name}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def fire_template(context, actor: dict, name: str, arg: str = "",
                  room: str = "pack-main", assignee_override: str = "", *,
                  create_task) -> dict:
    """Expand a template and delegate task creation to the existing task path."""
    if len(arg) > 1000:
        raise ValueError(f"arg too long: {len(arg)} > 1000")

    conn = context.connect()
    try:
        template = conn.execute(
            "SELECT title_tpl,body_tpl,assignee FROM task_templates WHERE name=?",
            (name,),
        ).fetchone()
    finally:
        conn.close()
    if not template:
        raise ValueError(f"no such template: {name}")
    if arg and "{arg}" not in template["title_tpl"] and "{arg}" not in template["body_tpl"]:
        raise ValueError(f"template '{name}' has no {{arg}} placeholder")
    if not arg.strip() and (
        "{arg}" in template["title_tpl"] or "{arg}" in template["body_tpl"]
    ):
        raise ValueError(f"template '{name}' requires an arg")

    title = template["title_tpl"].replace("{arg}", arg)
    body = template["body_tpl"].replace("{arg}", arg)
    effective_assignee = assignee_override or template["assignee"]
    if effective_assignee:
        conn = context.connect()
        try:
            member = conn.execute(
                "SELECT removed_at FROM members WHERE name=?",
                (effective_assignee,),
            ).fetchone()
        finally:
            conn.close()
        if member and member["removed_at"] is not None:
            raise ValueError(f"member is removed: {effective_assignee}")

    # Preserve the existing task_create path and its independent transaction.
    result = create_task(
        room=room, title=title, body=body,
        assignee_name=effective_assignee,
    )
    conn = context.connect()
    try:
        context.audit(
            conn, actor["name"], "template_fire",
            f"template {name} -> task {result['task_id']}",
        )
        conn.commit()
    finally:
        conn.close()
    result["template"] = name
    return result
