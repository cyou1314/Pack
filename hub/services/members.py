"""Member read operations. MCP registration and authorization remain in hub.py."""
from __future__ import annotations

import json
import sqlite3
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


def report_member_quota(context, me, week_used_pct=None, week_reset_at=None,
                        extra_tokens_left=None, *, validate_report, apply_quota,
                        quota_anomaly_jump) -> dict:
    """Persist self-reported quota, blocking forged drops and reset rollback."""
    problems = validate_report(week_used_pct, week_reset_at,
                                      extra_tokens_left)
    if problems:
        raise ValueError("invalid quota report: " + "; ".join(problems))
    conn = context.connect()
    try:
        with context.lock:
            row = conn.execute("SELECT * FROM members WHERE id=?",
                               (me["id"],)).fetchone()
            now = context.now()
            blocked_drop = None
            if week_used_pct is not None and row["quota_week_used_pct"] is not None \
                    and float(week_used_pct) < row["quota_week_used_pct"]:
                stored_reset = row["quota_week_reset_at"]
                cycle_rolled = (stored_reset is not None and stored_reset <= now
                                and week_reset_at is not None
                                and float(week_reset_at) > stored_reset)
                if not cycle_rolled:
                    blocked_drop = (row["quota_week_used_pct"], float(week_used_pct))
                    week_used_pct = None  # 该字段忽略，其余字段照常
            blocked_reset = None
            if week_reset_at is not None and row["quota_week_reset_at"] is not None \
                    and float(week_reset_at) < row["quota_week_reset_at"]:
                # 自报 reset_at 只许往后移：回挪能人为加速「周期到期」、
                # 配合下一报洗白用量，与降幅同罪拦下（真滚动要求新值更大，
                # 天然不受此限；调度员 member_quota_set 不受此限、可纠偏）。
                blocked_reset = (row["quota_week_reset_at"], float(week_reset_at))
                week_reset_at = None
            anomaly = None
            if week_used_pct is not None and row["quota_week_used_pct"] is not None:
                jump = float(week_used_pct) - row["quota_week_used_pct"]
                if jump > quota_anomaly_jump:
                    since = row["quota_updated_at"] or row["created_at"]
                    done_n = conn.execute(
                        "SELECT COUNT(*) AS n FROM audit WHERE actor=? AND action='task_complete' AND ts>=?",
                        (me["name"], since)).fetchone()["n"]
                    if not done_n:
                        anomaly = (row["quota_week_used_pct"], float(week_used_pct))
            fields = []
            if week_used_pct is not None or week_reset_at is not None \
                    or extra_tokens_left is not None:
                fields = apply_quota(conn, me["id"], week_used_pct,
                                      week_reset_at, extra_tokens_left, "self")
            if blocked_drop is not None:
                context.audit(conn, me["name"], "member_quota_drop_blocked",
                      f"{me['name']}: week_used_pct {blocked_drop[0]}->{blocked_drop[1]}"
                      f" blocked source=self stored_source={row['quota_source']}")
            if blocked_reset is not None:
                context.audit(conn, me["name"], "member_quota_reset_blocked",
                      f"{me['name']}: week_reset_at {blocked_reset[0]}->{blocked_reset[1]}"
                      " blocked (self-report may only move reset later) source=self")
            if anomaly is not None:
                context.audit(conn, me["name"], "quota_anomaly",
                      f"{me['name']}: week_used_pct jump {anomaly[0]}->{anomaly[1]}"
                      f" (+{anomaly[1] - anomaly[0]:.0f}) with no task_complete since last quota write source=self")
            if fields:
                context.audit(conn, me["name"], "member_quota_report",
                      f"{me['name']}: " + ", ".join(fields) + " source=self")
            conn.commit()
        if (blocked_drop is not None or blocked_reset is not None) and not fields:
            why = []
            if blocked_drop is not None:
                why.append(f"week_used_pct drop {blocked_drop[0]}->{blocked_drop[1]}"
                           " within the same cycle")
            if blocked_reset is not None:
                why.append(f"week_reset_at move-back {blocked_reset[0]}->{blocked_reset[1]}")
            raise ValueError("quota report blocked: " + "; ".join(why))
        out = dict(conn.execute(
            "SELECT name, quota_week_used_pct, quota_week_reset_at,"
            " quota_extra_left, quota_updated_at, quota_source"
            " FROM members WHERE id=?",
            (me["id"],)).fetchone())
        blocked_fields = []
        if blocked_drop is not None:
            blocked_fields.append("week_used_pct")
        if blocked_reset is not None:
            blocked_fields.append("week_reset_at")
        if blocked_fields:
            out["blocked_fields"] = blocked_fields
        return out
    finally:
        conn.close()


def remove_member(context, me, name: str, *, close_run, desktop_release_for_task,
                  desktop_snapshot, hash_token, token_factory) -> dict:
    """Soft-remove a member and atomically reclaim their tasks and resources."""
    if name == me["name"]:
        raise ValueError("cannot remove yourself")
    recalled = unassigned = expired = skills_cleared = 0
    lock_released = False
    with context.lock:
        conn = context.connect()
        try:
            row = conn.execute("SELECT * FROM members WHERE name=?",
                               (name,)).fetchone()
            if not row:
                raise ValueError(f"no such member: {name}")
            if row["role"] == "dispatcher":
                raise ValueError("cannot remove the dispatcher")
            if name == "owner":
                raise ValueError("cannot remove the owner")
            if row["removed_at"] is not None:
                raise ValueError(f"member already removed: {name}")
            now = context.clock()
            mid = row["id"]
            for t in conn.execute(
                    "SELECT * FROM tasks WHERE assignee=? AND status IN ('claimed','working')",
                    (mid,)).fetchall():
                cur = conn.execute(
                    "UPDATE tasks SET status='open', assignee=NULL, claim_token=NULL, lease_expires=NULL, updated_at=? WHERE id=? AND status IN ('claimed','working')",
                    (now, t["id"]))
                if cur.rowcount == 1:
                    close_run(conn, t["id"], "member_removed", me["name"])
                    desktop_release_for_task(conn, t["id"], me["name"],
                                              "member_removed")
                    recalled += 1
            unassigned = conn.execute(
                "UPDATE tasks SET assignee=NULL, updated_at=? WHERE assignee=? AND status='open'",
                (now, mid)).rowcount
            for x in conn.execute(
                    "SELECT * FROM task_transfers WHERE status='pending' AND (from_member=? OR to_member=?)",
                    (name, name)).fetchall():
                cur = conn.execute(
                    "UPDATE task_transfers SET status='expired' WHERE id=? AND status='pending'",
                    (x["id"],))
                if cur.rowcount != 1:
                    continue
                expired += 1
                t = conn.execute("SELECT * FROM tasks WHERE id=?",
                                 (x["task_id"],)).fetchone()
                if t and t["status"] in ("claimed", "working") and t["claim_token"] is None:
                    recv = conn.execute("SELECT id FROM members WHERE name=?",
                                        (x["to_member"],)).fetchone()
                    if recv and t["assignee"] == recv["id"]:
                        conn.execute(
                            "UPDATE tasks SET status='open', assignee=NULL, updated_at=? WHERE id=?",
                            (now, t["id"]))
            snap = desktop_snapshot(conn)
            if snap["held"] and snap["holder"] == name:
                conn.execute(
                    "UPDATE desktop_lock SET holder=NULL, task_id=NULL, acquired_at=NULL WHERE id=1")
                context.audit(conn, me["name"], "desktop_auto_release",
                      f"member_remove holder={name}")
                lock_released = True
            skills_cleared = conn.execute(
                "DELETE FROM member_skills WHERE member_id=?", (mid,)).rowcount
            conn.execute(
                "UPDATE members SET removed_at=?, token_hash=? WHERE id=?",
                (now, hash_token("revoked-" + token_factory()), mid))
            context.audit(conn, me["name"], "member_remove",
                  f"{name} recalled={recalled} unassigned={unassigned} "
                  f"transfers_expired={expired} skills_cleared={skills_cleared} "
                  f"lock_released={lock_released}")
            conn.commit()
        finally:
            conn.close()
    return {"removed": name, "tasks_recalled": recalled,
            "tasks_unassigned": unassigned, "transfers_expired": expired,
            "skills_cleared": skills_cleared, "lock_released": lock_released}


def register_member(context, actor, name: str, profile: str = "",
                    avatar_combo_json: str = "", provider: str = "", *,
                    token_factory, hash_token, avatar_for, used_combos,
                    make_avatar_combo, avatar_species, avatar_palettes,
                    avatar_accessories, avatar_bgs) -> dict:
    """Register a member and reserve a unique avatar combination atomically."""
    if len(profile or "") > 2000:
        raise ValueError("profile too long: > 2000 chars")
    if len(provider or "") > 64:
        raise ValueError("provider too long: > 64 chars")
    token = token_factory()

    with context.lock:
        conn = context.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute(
                "SELECT removed_at FROM members WHERE name=?", (name,),
            ).fetchone()
            if old is not None:
                if old["removed_at"] is not None:
                    raise ValueError(
                        f"member name reserved by removed member: {name}"
                    )
                raise ValueError(f"member name already exists: {name}")

            avatar = avatar_for(name)
            used = used_combos(conn)
            if avatar_combo_json:
                try:
                    requested = json.loads(avatar_combo_json)
                except Exception:
                    raise ValueError("avatar_combo: not valid JSON")
                if not isinstance(requested, dict):
                    raise ValueError("avatar_combo: must be a JSON object")
                selected = {}
                for key, allowed in (
                    ("species", avatar_species),
                    ("palette", avatar_palettes),
                    ("accessory", avatar_accessories),
                    ("bg", avatar_bgs),
                ):
                    value = requested.get(key)
                    if value is not None:
                        if value not in allowed:
                            raise ValueError(f"avatar_combo: bad {key}: {value}")
                        selected[key] = value
                merged = make_avatar_combo(name, used)
                merged.update(selected)
                combo = (
                    f"{merged['species']}|{merged['palette']}|"
                    f"{merged['accessory']}|{merged['bg']}"
                )
                if combo in used:
                    raise ValueError(f"avatar_combo: combo already taken: {combo}")
                merged["combo"] = combo
                avatar.update(merged)
            else:
                avatar.update(make_avatar_combo(name, used))

            login_state = "pending" if provider else None
            cursor = conn.execute(
                "INSERT INTO members(name,role,token_hash,profile,created_at,"
                "avatar,provider,login_state) VALUES(?,'member',?,?,?,?,?,?)",
                (name, hash_token(token), profile or None, context.clock(),
                 json.dumps(avatar), provider or None, login_state),
            )
            member_id = cursor.lastrowid
            context.audit(conn, actor["name"], "member_register", name)
            conn.commit()
        except sqlite3.IntegrityError as error:
            conn.rollback()
            raise ValueError(f"member name already exists: {name}") from error
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    return {
        "member_id": member_id, "name": name, "token": token,
        "avatar": avatar, "provider": provider or None,
        "login_state": login_state,
    }


def heartbeat(context, actor) -> dict:
    """Refresh the authenticated member's online timestamp."""
    conn = context.connect()
    try:
        conn.execute(
            "UPDATE members SET last_heartbeat=? WHERE id=?",
            (context.clock(), actor["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "member": actor["name"]}
