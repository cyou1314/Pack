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
