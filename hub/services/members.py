"""Member read operations. MCP registration and authorization remain in hub.py."""
from __future__ import annotations

import json
from typing import Any


def list_members(context, me: dict[str, Any], *, avatar_for, week_start, access_modes) -> dict[str, Any]:
    """Return member directory and sanitized external-member view."""
    conn = context.connect()
    try:
        rows = conn.execute(
            "SELECT id, name, role, profile, created_at, last_heartbeat, avatar,"
            " quota_week_used_pct, quota_week_reset_at, quota_extra_left, quota_updated_at,"
            " quota_source, provider, login_state, kind, display_name, bound_room_id,"
            " access_mode"
            " FROM members WHERE removed_at IS NULL ORDER BY id"
        ).fetchall()
        wk = week_start(context=context)
        counts = {r["assignee"]: r["n"] for r in conn.execute(
            "SELECT assignee, COUNT(*) AS n FROM tasks"
            " WHERE status='done' AND assignee IS NOT NULL AND updated_at>=?"
            " GROUP BY assignee", (wk,)).fetchall()}
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["avatar"] = json.loads(d["avatar"]) if d.get("avatar") else avatar_for(d["name"])
        except Exception:
            d["avatar"] = avatar_for(d["name"])
        d["week_done"] = counts.get(d["id"], 0)
        # staging51：新字段纯追加（老成员 kind 回 "internal"、
        # display_name 回 name、bound_room 回 None，向后兼容）。
        d["kind"] = d.get("kind") or "internal"
        if not d.get("display_name"):
            d["display_name"] = d["name"]
        # staging56：access_mode 归一回读（NULL/脏值一律 full）。
        d["access_mode"] = (d.get("access_mode")
                            if d.get("access_mode") in access_modes
                            else "full")
        out.append(d)
    if (me.get("kind") or "") == "external":
        # 件二脱敏视图：external 只看得到身份类字段，额度、
        # login_state、week_done 一律剥离。
        keys = ("name", "display_name", "role", "kind", "provider",
                "avatar", "last_heartbeat")
        out = [{k: d.get(k) for k in keys} for d in out]
    return {"members": out}


def set_login_state(context, actor, name: str, state: str, login_states) -> dict:
    """Authenticated member operation; caller enforces dispatcher permission."""
    if state not in login_states:
        raise ValueError(
            f"bad login_state: {state!r} (allowed: {', '.join(login_states)})")
    with context.lock:
        conn = context.connect()
        try:
            row = conn.execute(
                "SELECT id, removed_at, provider FROM members WHERE name=?",
                (name,)).fetchone()
            if not row:
                raise ValueError(f"no such member: {name}")
            if row["removed_at"] is not None:
                raise ValueError(f"member removed: {name}")
            conn.execute("UPDATE members SET login_state=? WHERE id=?",
                         (state, row["id"]))
            context.audit(conn, actor["name"], "member_login_state_set", f"{name}: {state}")
            conn.commit()
            out = {"name": name, "provider": row["provider"], "login_state": state}
        finally:
            conn.close()
    return out


def set_access_mode(context, actor, name: str, mode: str, access_modes) -> dict:
    """Authenticated member operation; caller enforces dispatcher permission."""
    if mode not in access_modes:
        raise ValueError(
            f"bad access mode: {mode} (want full/workspace/readonly)")
    with context.lock:
        conn = context.connect()
        try:
            row = conn.execute(
                "SELECT id, removed_at, role, access_mode FROM members"
                " WHERE name=?", (name,)).fetchone()
            if not row:
                raise ValueError(f"no such member: {name}")
            if row["removed_at"] is not None:
                raise ValueError(f"member removed: {name}")
            if row["role"] == "dispatcher":
                raise ValueError(
                    "cannot set access mode for the dispatcher")
            if row["role"] == "owner":
                raise ValueError("cannot set access mode for the owner")
            old = (row["access_mode"]
                   if row["access_mode"] in access_modes else "full")
            conn.execute(
                "UPDATE members SET access_mode=? WHERE id=?",
                (mode, row["id"]))
            context.audit(conn, actor["name"], "member_access_mode_set",
                  f"{name}: {old} -> {mode}")
            conn.commit()
        finally:
            conn.close()
    return {"name": name, "access_mode": mode}


def rename_member(context, actor, name: str, display_name: str) -> dict:
    """Authenticated member operation; caller enforces dispatcher permission."""
    dn = (display_name or "").strip()
    if not dn or len(dn) > 80:
        raise ValueError("display_name must be 1-80 chars")
    with context.lock:
        conn = context.connect()
        try:
            row = conn.execute(
                "SELECT id, removed_at FROM members WHERE name=?",
                (name,)).fetchone()
            if not row:
                raise ValueError(f"no such member: {name}")
            if row["removed_at"] is not None:
                raise ValueError(f"member removed: {name}")
            conn.execute("UPDATE members SET display_name=? WHERE id=?",
                         (dn, row["id"]))
            context.audit(conn, actor["name"], "member_rename", f"{name} -> {dn}")
            conn.commit()
        finally:
            conn.close()
    return {"name": name, "display_name": dn}


def rotate_member_token(context, actor, name: str, *, token_factory, hash_token) -> dict:
    """Rotate one active non-dispatcher token; never store or audit plaintext."""
    token = token_factory()
    with context.lock:
        conn = context.connect()
        try:
            row = conn.execute(
                "SELECT id, removed_at, role FROM members WHERE name=?",
                (name,),
            ).fetchone()
            if not row:
                raise ValueError(f"no such member: {name}")
            if row["removed_at"] is not None:
                raise ValueError(f"member removed: {name}")
            if row["role"] == "dispatcher":
                raise ValueError(
                    "cannot rotate the dispatcher's token with this tool")
            conn.execute(
                "UPDATE members SET token_hash=? WHERE id=?",
                (hash_token(token), row["id"]),
            )
            context.audit(conn, actor["name"], "member_token_rotate", name)
            conn.commit()
        finally:
            conn.close()
    return {"name": name, "token": token}


def set_member_quota(context, actor, member_name: str, week_used_pct=None,
                     week_reset_at=None, extra_tokens_left=None, *, apply_quota) -> dict:
    """Apply dispatcher-supplied quota fields without touching other members."""
    if week_used_pct is None and week_reset_at is None and extra_tokens_left is None:
        raise ValueError("nothing to update: give at least one quota field")
    if week_used_pct is not None and not (0 <= week_used_pct <= 100):
        raise ValueError(f"week_used_pct out of range 0-100: {week_used_pct}")
    if week_reset_at is not None and not (week_reset_at > 0):
        raise ValueError(f"week_reset_at must be epoch seconds > 0: {week_reset_at}")
    if extra_tokens_left is not None and extra_tokens_left < 0:
        raise ValueError(f"extra_tokens_left must be >= 0: {extra_tokens_left}")
    conn = context.connect()
    try:
        row = conn.execute("SELECT id, removed_at FROM members WHERE name=?", (member_name,)).fetchone()
        if not row:
            raise ValueError(f"no such member: {member_name}")
        if row["removed_at"] is not None:
            # staging49：已移除成员不得再被设额度（写入即污染其历史行，
            # 且 member_list 已隐藏、App 无从发现这笔写入）
            raise ValueError(f"member is removed: {member_name}")
        fields = apply_quota(conn, row["id"], week_used_pct,
                              week_reset_at, extra_tokens_left, "dispatcher")
        context.audit(conn, actor["name"], "member_quota_set",
              f"{member_name}: " + ", ".join(fields) + " source=dispatcher")
        conn.commit()
        out = dict(conn.execute(
            "SELECT name, quota_week_used_pct, quota_week_reset_at,"
            " quota_extra_left, quota_updated_at, quota_source"
            " FROM members WHERE id=?",
            (row["id"],)).fetchone())
    finally:
        conn.close()
    return out
