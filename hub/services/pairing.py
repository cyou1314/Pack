"""External pairing redemption with cross-process code and throttle serialization."""
from __future__ import annotations

import json


def redeem_pair(context, code, *, json_response, hash_token, token_factory,
                external_enabled, avatar_for, used_combos, make_avatar_combo,
                global_fail_window: int, global_fail_max: int,
                code_fail_lock: int):
    """Redeem one bound pairing code, atomically enforcing all anti-abuse gates."""
    with context.lock:
        conn = context.connect()
        try:
            # Serialize the global failure window, code state check, member
            # creation/reclaim and redeemed marker across independent Hub
            # processes sharing the same SQLite database.
            conn.execute("BEGIN IMMEDIATE")
            now = context.clock()
            failures = conn.execute(
                "SELECT COUNT(*) AS n FROM pair_attempts WHERE success=0 AND ts>=?",
                (now - global_fail_window,),
            ).fetchone()["n"]
            if failures >= global_fail_max:
                context.audit(
                    conn, "pair", "pair_global_throttle",
                    f"fails={failures} window={global_fail_window}s",
                )
                conn.commit()
                return json_response(
                    {"error": "too many failed attempts; try again later"},
                    status_code=429,
                )
            if not external_enabled(conn):
                context.audit(conn, "pair", "pair_rejected_gate_off", "")
                conn.commit()
                return json_response(
                    {"error": "pairing is currently disabled"},
                    status_code=403,
                )
            if not code or not isinstance(code, str):
                conn.execute(
                    "INSERT INTO pair_attempts(ts,success) VALUES(?,0)", (now,),
                )
                context.audit(conn, "pair", "pair_failed", "reason=missing_code")
                conn.commit()
                return json_response({"error": "invalid pair code"}, status_code=400)

            row = conn.execute(
                "SELECT * FROM pair_codes WHERE code_hash=?",
                (hash_token(code),),
            ).fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO pair_attempts(ts,success) VALUES(?,0)", (now,),
                )
                context.audit(conn, "pair", "pair_failed", "reason=unknown_code")
                conn.commit()
                return json_response({"error": "invalid pair code"}, status_code=400)

            def known_fail(reason):
                fail_count = row["fail_count"] + 1
                newly_locked = (
                    fail_count >= code_fail_lock and not row["locked_at"]
                )
                if newly_locked:
                    conn.execute(
                        "UPDATE pair_codes SET fail_count=?,locked_at=? WHERE id=?",
                        (fail_count, now, row["id"]),
                    )
                else:
                    conn.execute(
                        "UPDATE pair_codes SET fail_count=? WHERE id=?",
                        (fail_count, row["id"]),
                    )
                conn.execute(
                    "INSERT INTO pair_attempts(ts,success) VALUES(?,0)", (now,),
                )
                context.audit(
                    conn, "pair", "pair_failed",
                    f"code_id={row['id']} reason={reason} fail_count={fail_count}",
                )
                if newly_locked:
                    context.audit(
                        conn, "pair", "pair_code_locked",
                        f"code_id={row['id']} member={row['member_name']}"
                        f" fail_count={fail_count}",
                    )
                conn.commit()
                return json_response(
                    {"error": "invalid pair code"}, status_code=400,
                )

            if row["revoked_at"]:
                return known_fail("revoked")
            if row["redeemed_at"]:
                return known_fail("already_redeemed")
            if row["locked_at"]:
                return known_fail("locked")
            if row["expires_at"] <= now:
                return known_fail("expired")

            old = conn.execute(
                "SELECT * FROM members WHERE name=?", (row["member_name"],),
            ).fetchone()
            reclaiming = False
            if old is not None:
                if old["removed_at"] is not None:
                    return known_fail("name_reserved")
                if (old["kind"] or "") != "external":
                    return known_fail("name_taken")
                if not row["reclaim"]:
                    return known_fail("name_active_external")
                reclaiming = True

            room = conn.execute(
                "SELECT id,name FROM rooms WHERE id=?", (row["bound_room_id"],),
            ).fetchone()
            if not room:
                return known_fail("room_missing")

            token = token_factory()
            if reclaiming:
                # Reclaim rotates only token/provider; room binding, join
                # watermark, read cursor, avatar and created_at remain intact.
                member_id = old["id"]
                conn.execute(
                    "UPDATE members SET token_hash=?,provider=? WHERE id=?",
                    (hash_token(token), row["provider"] or old["provider"], member_id),
                )
                display_name = old["display_name"] or old["name"]
                previous_room = conn.execute(
                    "SELECT name FROM rooms WHERE id=?",
                    (old["bound_room_id"],),
                ).fetchone()
                response_room = (
                    previous_room["name"] if previous_room else room["name"]
                )
            else:
                joined = conn.execute(
                    "SELECT COALESCE(MAX(id),0) AS m FROM messages WHERE room_id=?",
                    (room["id"],),
                ).fetchone()["m"]
                avatar = avatar_for(row["member_name"])
                avatar.update(make_avatar_combo(
                    row["member_name"], used_combos(conn),
                ))
                cursor = conn.execute(
                    "INSERT INTO members(name,role,token_hash,profile,created_at,"
                    "avatar,provider,kind,display_name,bound_room_id,joined_msg_id) "
                    "VALUES(?,'member',?,NULL,?,?,?,?,?,?,?)",
                    (
                        row["member_name"], hash_token(token), now,
                        json.dumps(avatar), row["provider"] or None,
                        "external", row["display_name"] or row["member_name"],
                        room["id"], joined,
                    ),
                )
                member_id = cursor.lastrowid
                conn.execute(
                    "INSERT OR REPLACE INTO member_read_cursors"
                    "(member_id,room_id,cursor_id) VALUES(?,?,?)",
                    (member_id, room["id"], joined),
                )
                display_name = row["display_name"] or row["member_name"]
                response_room = room["name"]

            conn.execute(
                "UPDATE pair_codes SET redeemed_member_id=?,redeemed_at=? "
                "WHERE id=? AND redeemed_at IS NULL",
                (member_id, now, row["id"]),
            )
            conn.execute(
                "INSERT INTO pair_attempts(ts,success) VALUES(?,1)", (now,),
            )
            context.audit(
                conn, "pair", "pair_redeem",
                f"code_id={row['id']} member={row['member_name']}"
                f" member_id={member_id} reclaim={1 if reclaiming else 0}",
            )
            conn.commit()
            return json_response({
                "member": row["member_name"], "display_name": display_name,
                "member_id": member_id, "token": token, "room": response_room,
            })
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def create_pair_code(context, actor: dict, member_name: str, provider: str,
                     room: str, display_name: str = "", reclaim: bool = False, *,
                     pair_providers, code_ttl: int, token_factory,
                     hash_token, external_enabled) -> dict:
    """Create one room-bound, single-use pairing credential."""
    name = (member_name or "").strip()
    if not name or len(name) > 64 or any(ch.isspace() for ch in name) \
            or "/" in name or "\\" in name or "@" in name or "＠" in name:
        raise ValueError(
            "member_name must be a short id (no whitespace/slashes/@)"
        )
    if provider not in pair_providers:
        raise ValueError(f"provider must be one of {list(pair_providers)}")
    if len(display_name or "") > 80:
        raise ValueError("display_name too long: > 80 chars")

    code = token_factory()
    now = context.clock()
    expires_at = now + code_ttl
    with context.lock:
        conn = context.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if not external_enabled(conn):
                context.audit(
                    conn, actor["name"], "pair_code_gate_blocked",
                    f"member={name}",
                )
                conn.commit()
                raise ValueError(
                    "external access is disabled (external_gate off)"
                )
            room_row = conn.execute(
                "SELECT id FROM rooms WHERE name=?", (room,),
            ).fetchone()
            if not room_row:
                raise ValueError(f"no such room: {room}")
            old = conn.execute(
                "SELECT removed_at,kind,bound_room_id FROM members WHERE name=?",
                (name,),
            ).fetchone()
            if reclaim and old is None:
                raise ValueError(
                    f"reclaim target is not an existing external member: {name}"
                )
            if old is not None:
                if old["removed_at"] is not None:
                    raise ValueError(
                        f"member name reserved by removed member: {name}"
                    )
                if (old["kind"] or "") != "external":
                    raise ValueError(
                        f"member name taken by an internal member: {name}"
                    )
                if not reclaim:
                    raise ValueError(
                        f"member {name} is an active external member;"
                        " use reclaim=True to rotate its token"
                    )
                if old["bound_room_id"] != room_row["id"]:
                    raise ValueError(
                        f"reclaim room mismatch: member {name} is"
                        f" bound to room_id {old['bound_room_id']},"
                        f" not room {room}"
                    )
            cursor = conn.execute(
                "INSERT INTO pair_codes(code_hash,member_name,provider,"
                "bound_room_id,display_name,reclaim,created_by,created_at,"
                "expires_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    hash_token(code), name, provider, room_row["id"],
                    display_name or None, 1 if reclaim else 0,
                    actor["name"], now, expires_at,
                ),
            )
            code_id = cursor.lastrowid
            context.audit(
                conn, actor["name"], "pair_code_create",
                f"code_id={code_id} member={name} provider={provider}"
                f" room_id={room_row['id']} reclaim={1 if reclaim else 0}",
            )
            conn.commit()
            return {
                "pair_code": code, "code_id": code_id,
                "member_name": name, "provider": provider,
                "room": room,
                "display_name": display_name or name,
                "reclaim": bool(reclaim), "expires_at": expires_at,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def list_pair_codes(context) -> dict:
    conn = context.connect()
    try:
        rows = conn.execute(
            "SELECT c.*,r.name AS room_name FROM pair_codes c "
            "JOIN rooms r ON r.id=c.bound_room_id "
            "ORDER BY c.id DESC LIMIT 200"
        ).fetchall()
    finally:
        conn.close()
    now = context.clock()
    output = []
    for row in rows:
        if row["revoked_at"]:
            status = "revoked"
        elif row["redeemed_at"]:
            status = "redeemed"
        elif row["locked_at"]:
            status = "locked"
        elif row["expires_at"] <= now:
            status = "expired"
        else:
            status = "unused"
        output.append({
            "code_id": row["id"], "member_name": row["member_name"],
            "provider": row["provider"], "room": row["room_name"],
            "room_id": row["bound_room_id"],
            "display_name": row["display_name"],
            "reclaim": bool(row["reclaim"]), "status": status,
            "created_by": row["created_by"], "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "redeemed_member_id": row["redeemed_member_id"],
            "redeemed_at": row["redeemed_at"],
            "revoked_at": row["revoked_at"],
            "fail_count": row["fail_count"], "locked_at": row["locked_at"],
        })
    return {"pair_codes": output}


def revoke_pair_code(context, actor: dict, code_id: int) -> dict:
    with context.lock:
        conn = context.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM pair_codes WHERE id=?", (code_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"no such pair code: {code_id}")
            if row["redeemed_at"]:
                raise ValueError(f"pair code {code_id} already redeemed")
            if row["revoked_at"]:
                raise ValueError(f"pair code {code_id} already revoked")
            cursor = conn.execute(
                "UPDATE pair_codes SET revoked_at=? WHERE id=? "
                "AND revoked_at IS NULL AND redeemed_at IS NULL",
                (context.clock(), code_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"pair code {code_id} cannot be revoked now")
            context.audit(
                conn, actor["name"], "pair_code_revoke",
                f"code_id={code_id} member={row['member_name']}",
            )
            conn.commit()
            return {"code_id": code_id, "revoked": True}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def set_external_gate(context, actor: dict, enabled, reason: str = "", *,
                      external_enabled) -> dict:
    """Read or update the global external-member gate."""
    if enabled is None:
        conn = context.connect()
        try:
            return {"external_enabled": external_enabled(conn)}
        finally:
            conn.close()

    with context.lock:
        conn = context.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO hub_settings(key,value) VALUES('external_enabled',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("1" if enabled else "0",),
            )
            context.audit(
                conn, actor["name"], "external_gate_set",
                f"enabled={1 if enabled else 0}"
                + (f" reason={reason}" if reason else ""),
            )
            conn.commit()
            return {"external_enabled": bool(enabled)}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
