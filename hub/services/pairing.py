"""External pairing redemption with cross-process code and throttle serialization."""
from __future__ import annotations


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
                        __import__("json").dumps(avatar), row["provider"] or None,
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
