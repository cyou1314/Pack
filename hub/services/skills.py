"""Skill registry services with serialized version allocation and assignment."""
from __future__ import annotations

import json


def _get_skill(conn, name: str):
    normalized = (name or "").strip()
    row = conn.execute(
        "SELECT * FROM skills WHERE name=?", (normalized,)
    ).fetchone()
    if not row:
        raise ValueError(f"no such skill: {normalized}")
    return row


def publish_skill(context, actor: dict, name: str, body: str,
                  description: str = "", *,
                  body_limit: int, description_limit: int) -> dict:
    """Publish a new immutable skill version."""
    name = (name or "").strip()
    if not name:
        raise ValueError("skill name must not be empty")
    if not isinstance(body, str) or body == "":
        raise ValueError("skill body must be a non-empty string")
    if len(body) > body_limit:
        raise ValueError(json.dumps(
            {"error": "skill_limit_exceeded", "skill": name,
             "limit_chars": body_limit, "current_chars": len(body),
             "hint": "正文超限：拆成多个 skill 分别 publish 再一并 "
                     "skill_assign，或删减正文后重试"},
            ensure_ascii=False,
        ))
    if description and len(description) > description_limit:
        raise ValueError(json.dumps(
            {"error": "skill_desc_limit_exceeded", "skill": name,
             "limit_chars": description_limit,
             "current_chars": len(description),
             "hint": "description 是一句话简介，压到 200 字内"},
            ensure_ascii=False,
        ))

    with context.lock:
        conn = context.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            now = context.clock()
            skill = conn.execute(
                "SELECT * FROM skills WHERE name=?", (name,)
            ).fetchone()
            if not skill:
                cursor = conn.execute(
                    "INSERT INTO skills(name,description,current_version,updated_at) "
                    "VALUES(?,?,1,?)",
                    (name, description or "", now),
                )
                skill_id, new_version = cursor.lastrowid, 1
            else:
                skill_id = skill["id"]
                new_version = skill["current_version"] + 1
                conn.execute(
                    "UPDATE skills SET current_version=?, updated_at=?, description=? "
                    "WHERE id=?",
                    (new_version, now,
                     description if description else skill["description"],
                     skill_id),
                )
            conn.execute(
                "INSERT INTO skill_versions(skill_id,version,body,created_by,created_at) "
                "VALUES(?,?,?,?,?)",
                (skill_id, new_version, body, actor["name"], now),
            )
            context.audit(
                conn, actor["name"], "skill_publish",
                json.dumps({
                    "skill": name, "version": new_version,
                }, ensure_ascii=False),
            )
            conn.commit()
            return {"skill_id": skill_id, "name": name, "version": new_version}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def list_skills(context, actor: dict) -> dict:
    """List the full registry for dispatcher or enabled assignments for members."""
    conn = context.connect()
    try:
        if actor["role"] == "dispatcher":
            rows = conn.execute(
                "SELECT id,name,description,current_version,updated_at "
                "FROM skills ORDER BY name"
            ).fetchall()
            assignments = conn.execute(
                "SELECT ms.skill_id AS sid,m.name AS mname,ms.enabled AS en "
                "FROM member_skills ms JOIN members m ON m.id=ms.member_id "
                "ORDER BY m.name"
            ).fetchall()
            by_skill = {}
            for row in assignments:
                by_skill.setdefault(row["sid"], []).append({
                    "member": row["mname"], "enabled": bool(row["en"]),
                })
            return {"skills": [{
                "name": row["name"], "description": row["description"],
                "version": row["current_version"],
                "updated_at": row["updated_at"],
                "assignees": by_skill.get(row["id"], []),
            } for row in rows]}

        rows = conn.execute(
            "SELECT s.name,s.description,s.current_version,s.updated_at "
            "FROM skills s JOIN member_skills ms ON ms.skill_id=s.id "
            "WHERE ms.member_id=? AND ms.enabled=1 ORDER BY s.name",
            (actor["id"],),
        ).fetchall()
        return {"skills": [{
            "name": row["name"], "description": row["description"],
            "version": row["current_version"], "updated_at": row["updated_at"],
        } for row in rows]}
    finally:
        conn.close()


def read_skill(context, actor: dict, name: str) -> dict:
    """Read current skill text only for dispatcher or enabled assignee."""
    conn = context.connect()
    try:
        if actor["role"] == "dispatcher":
            skill = _get_skill(conn, name)
        else:
            skill = conn.execute(
                "SELECT * FROM skills WHERE name=?",
                ((name or "").strip(),),
            ).fetchone()
            assignment = None
            if skill:
                assignment = conn.execute(
                    "SELECT enabled FROM member_skills "
                    "WHERE member_id=? AND skill_id=?",
                    (actor["id"], skill["id"]),
                ).fetchone()
            if not skill or not assignment or not assignment["enabled"]:
                raise ValueError(
                    f"forbidden: skill '{name}' is not assigned to {actor['name']}"
                )

        version = conn.execute(
            "SELECT body FROM skill_versions WHERE skill_id=? AND version=?",
            (skill["id"], skill["current_version"]),
        ).fetchone()
        if not version:
            raise ValueError(
                f"skill data inconsistent: skill '{skill['name']}' "
                f"version {skill['current_version']} body missing"
            )
        return {
            "name": skill["name"], "version": skill["current_version"],
            "description": skill["description"], "body": version["body"],
        }
    finally:
        conn.close()


def set_skill_assignment(context, actor: dict, name: str, member_name: str,
                          enabled: bool, action: str) -> dict:
    """Enable/disable one member assignment; unassigning absent rows is a no-op."""
    with context.lock:
        conn = context.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            skill = _get_skill(conn, name)
            member = conn.execute(
                "SELECT * FROM members WHERE name=?", (member_name,)
            ).fetchone()
            if not member:
                raise ValueError(f"no such member: {member_name}")
            if member["removed_at"] is not None:
                raise ValueError(f"member is removed: {member_name}")
            existing = conn.execute(
                "SELECT enabled FROM member_skills "
                "WHERE member_id=? AND skill_id=?",
                (member["id"], skill["id"]),
            ).fetchone()
            if not enabled and existing is None:
                return {
                    "name": skill["name"], "member": member_name,
                    "enabled": False,
                }
            conn.execute(
                "INSERT INTO member_skills(member_id,skill_id,enabled) VALUES(?,?,?) "
                "ON CONFLICT(member_id,skill_id) DO UPDATE SET enabled=excluded.enabled",
                (member["id"], skill["id"], 1 if enabled else 0),
            )
            context.audit(
                conn, actor["name"], action,
                json.dumps({
                    "skill": skill["name"],
                    "version": skill["current_version"],
                    "member": member_name,
                }, ensure_ascii=False),
            )
            conn.commit()
            return {
                "name": skill["name"], "member": member_name,
                "enabled": bool(enabled),
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def rollback_skill(context, actor: dict, name: str, version: int) -> dict:
    """Append a new current version using an existing historical body."""
    with context.lock:
        conn = context.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            skill = _get_skill(conn, name)
            source = conn.execute(
                "SELECT body FROM skill_versions WHERE skill_id=? AND version=?",
                (skill["id"], version),
            ).fetchone()
            if not source:
                raise ValueError(
                    f"no such version: skill '{skill['name']}' version {version} "
                    f"(current_version={skill['current_version']})"
                )
            new_version = skill["current_version"] + 1
            now = context.clock()
            conn.execute(
                "INSERT INTO skill_versions(skill_id,version,body,created_by,created_at) "
                "VALUES(?,?,?,?,?)",
                (skill["id"], new_version, source["body"], actor["name"], now),
            )
            conn.execute(
                "UPDATE skills SET current_version=?, updated_at=? WHERE id=?",
                (new_version, now, skill["id"]),
            )
            context.audit(
                conn, actor["name"], "skill_rollback",
                json.dumps({
                    "skill": skill["name"], "restored_from": version,
                    "new_version": new_version,
                }, ensure_ascii=False),
            )
            conn.commit()
            return {
                "name": skill["name"], "version": new_version,
                "restored_from": version,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
