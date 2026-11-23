#!/usr/bin/env python3
"""Pair redemption service state, reclaim, throttling and race tests."""
import json
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.pairing import (
    create_pair_code, list_pair_codes, redeem_pair, revoke_pair_code,
    set_external_gate,
)


class PairResponse(dict):
    def __init__(self, body, status_code=200):
        super().__init__(body)
        self.status_code = status_code


class PairingServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-pairing-service-")
        root = Path(self.temp.name)
        self.context = HubContext(
            base_dir=str(root), db_path=str(root / "hub.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(), clock=lambda: 12500.0,
        )
        hub.init(self.context)
        self.db = self.context.connect()
        self.dispatcher = dict(self.db.execute(
            "SELECT * FROM members WHERE role='dispatcher'"
        ).fetchone())
        self.code = "pk-pair-test-code"
        self.code_id = self.create_code("alice", self.code)
        self.enabled = True

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def create_code(self, name, code, *, reclaim=False, provider="generic",
                    room_id=1, display_name="Alice"):
        cursor = self.db.execute(
            "INSERT INTO pair_codes(code_hash,member_name,provider,bound_room_id,"
            "display_name,reclaim,created_by,created_at,expires_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (hub.hash_token(code), name, provider, room_id, display_name,
             1 if reclaim else 0, self.dispatcher["name"], 12000, 90000),
        )
        self.db.commit()
        return cursor.lastrowid

    def response(self, body, status_code=200):
        return PairResponse(body, status_code)

    def redeem(self, code=None, context=None, **overrides):
        args = {
            "json_response": self.response,
            "hash_token": hub.hash_token,
            "token_factory": lambda: "pk-mem-issued-token",
            "external_enabled": lambda conn: self.enabled,
            "avatar_for": hub.avatar_for,
            "used_combos": hub._used_combos,
            "make_avatar_combo": hub._avatar_combo,
            "global_fail_window": hub.PAIR_GLOBAL_FAIL_WINDOW,
            "global_fail_max": hub.PAIR_GLOBAL_FAIL_MAX,
            "code_fail_lock": hub.PAIR_CODE_FAIL_LOCK,
        }
        args.update(overrides)
        return redeem_pair(context or self.context, code, **args)

    def count(self, table, where="", args=()):
        return self.db.execute(
            f"SELECT COUNT(*) FROM {table} {where}", args,
        ).fetchone()[0]

    def test_success_creates_bound_external_member_and_cursor(self):
        self.db.execute(
            "INSERT INTO messages(room_id,author_id,kind,body,created_at) "
            "VALUES(1,?,'chat','before join',12490)",
            (self.dispatcher["id"],),
        )
        self.db.commit()
        result = self.redeem(self.code)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result["member"], "alice")
        self.assertEqual(result["room"], "pack-main")
        member = self.db.execute(
            "SELECT * FROM members WHERE name='alice'"
        ).fetchone()
        self.assertEqual(member["kind"], "external")
        self.assertEqual(member["bound_room_id"], 1)
        self.assertEqual(member["joined_msg_id"], 1)
        self.assertEqual(member["token_hash"], hub.hash_token("pk-mem-issued-token"))
        cursor = self.db.execute(
            "SELECT cursor_id FROM member_read_cursors WHERE member_id=? AND room_id=1",
            (member["id"],),
        ).fetchone()
        self.assertEqual(cursor["cursor_id"], 1)
        self.assertEqual(self.db.execute(
            "SELECT redeemed_member_id FROM pair_codes WHERE id=?",
            (self.code_id,),
        ).fetchone()[0], member["id"])
        self.assertEqual(self.count("pair_attempts", "WHERE success=1"), 1)

    def test_missing_unknown_and_redeemed_codes_are_rejected(self):
        missing = self.redeem(None)
        unknown = self.redeem("not-known")
        self.assertEqual((missing.status_code, unknown.status_code), (400, 400))
        self.assertEqual(self.count("pair_attempts", "WHERE success=0"), 2)
        self.assertEqual(self.redeem(self.code).status_code, 200)
        again = self.redeem(self.code)
        self.assertEqual(again.status_code, 400)
        self.assertEqual(self.count("members", "WHERE name='alice'"), 1)

    def test_external_gate_and_global_throttle(self):
        self.enabled = False
        result = self.redeem(self.code)
        self.assertEqual(result.status_code, 403)
        self.assertEqual(self.count("pair_attempts"), 0)
        self.enabled = True
        self.db.executemany(
            "INSERT INTO pair_attempts(ts,success) VALUES(?,0)",
            [(12499.0,)] * hub.PAIR_GLOBAL_FAIL_MAX,
        )
        self.db.commit()
        throttled = self.redeem(self.code)
        self.assertEqual(throttled.status_code, 429)
        self.assertIsNone(self.db.execute(
            "SELECT redeemed_at FROM pair_codes WHERE id=?", (self.code_id,),
        ).fetchone()[0])

    def test_reclaim_rotates_token_only_and_preserves_room_watermark(self):
        self.db.execute(
            "INSERT INTO members(name,role,token_hash,created_at,avatar,provider,"
            "kind,display_name,bound_room_id,joined_msg_id) "
            "VALUES('alice','member','old-hash',10000,'{\"glyph\":\"A\"}',"
            "'old-provider','external','Old Alice',1,77)"
        )
        member_id = self.db.execute(
            "SELECT id FROM members WHERE name='alice'"
        ).fetchone()[0]
        self.db.execute(
            "INSERT INTO member_read_cursors(member_id,room_id,cursor_id) "
            "VALUES(?,?,66)", (member_id, 1),
        )
        self.db.execute(
            "UPDATE pair_codes SET reclaim=1 WHERE id=?", (self.code_id,),
        )
        self.db.commit()
        result = self.redeem(self.code)
        self.assertEqual(result.status_code, 200)
        row = self.db.execute(
            "SELECT token_hash,provider,kind,display_name,bound_room_id,"
            "joined_msg_id,avatar FROM members WHERE id=?", (member_id,),
        ).fetchone()
        self.assertEqual(row["token_hash"], hub.hash_token("pk-mem-issued-token"))
        self.assertEqual(row["provider"], "generic")
        self.assertEqual(row["kind"], "external")
        self.assertEqual(row["display_name"], "Old Alice")
        self.assertEqual(row["bound_room_id"], 1)
        self.assertEqual(row["joined_msg_id"], 77)
        self.assertEqual(row["avatar"], '{"glyph":"A"}')
        self.assertEqual(self.db.execute(
            "SELECT cursor_id FROM member_read_cursors WHERE member_id=? AND room_id=1",
            (member_id,),
        ).fetchone()[0], 66)

    def test_internal_name_collision_is_audited_as_generic_failure(self):
        self.db.execute(
            "INSERT INTO members(name,role,token_hash,created_at) "
            "VALUES('alice','member','internal-hash',12000)"
        )
        self.db.commit()
        result = self.redeem(self.code)
        self.assertEqual(result.status_code, 400)
        self.assertEqual(self.count("members", "WHERE name='alice'"), 1)
        self.assertEqual(self.db.execute(
            "SELECT fail_count FROM pair_codes WHERE id=?", (self.code_id,),
        ).fetchone()[0], 1)

    def test_expired_revoked_and_locked_codes_fail(self):
        self.db.execute(
            "UPDATE pair_codes SET expires_at=12001 WHERE id=?", (self.code_id,),
        )
        self.db.commit()
        self.assertEqual(self.redeem(self.code).status_code, 400)
        self.db.execute(
            "UPDATE pair_codes SET expires_at=90000,revoked_at=12499 WHERE id=?",
            (self.code_id,),
        )
        self.db.commit()
        self.assertEqual(self.redeem(self.code).status_code, 400)
        self.db.execute(
            "UPDATE pair_codes SET revoked_at=NULL,locked_at=12499 WHERE id=?",
            (self.code_id,),
        )
        self.db.commit()
        self.assertEqual(self.redeem(self.code).status_code, 400)

    def test_audit_failure_rolls_back_member_and_code_redemption(self):
        self.db.execute(
            "CREATE TRIGGER reject_pair_redeem BEFORE INSERT ON audit "
            "WHEN NEW.action='pair_redeem' BEGIN "
            "SELECT RAISE(ABORT,'pair audit rejected'); END"
        )
        self.db.commit()
        with self.assertRaisesRegex(Exception, "pair audit rejected"):
            self.redeem(self.code)
        self.assertEqual(self.count("members", "WHERE name='alice'"), 0)
        self.assertIsNone(self.db.execute(
            "SELECT redeemed_at FROM pair_codes WHERE id=?", (self.code_id,),
        ).fetchone()[0])
        self.assertEqual(self.count("pair_attempts"), 0)

    def test_independent_contexts_cannot_redeem_same_code_twice(self):
        root = Path(self.temp.name)
        independent = HubContext(
            base_dir=str(root), db_path=str(root / "hub.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(), clock=lambda: 12500.0,
        )
        warm = independent.connect()
        warm.close()
        barrier = threading.Barrier(2)
        results, errors = [], []

        def submit(context):
            try:
                barrier.wait(timeout=10)
                results.append(self.redeem(context=context, code=self.code))
            except Exception as error:
                errors.append(error)

        threads = [
            threading.Thread(target=submit, args=(self.context,)),
            threading.Thread(target=submit, args=(independent,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertCountEqual([result.status_code for result in results], [200, 400])
        self.assertEqual(self.count("members", "WHERE name='alice'"), 1)
        self.assertEqual(self.count("audit", "WHERE action='pair_redeem'"), 1)
        self.assertEqual(self.count("pair_attempts", "WHERE success=1"), 1)


    def issue_code(self, name="new-external", provider="generic",
                   room="pack-main", display_name="", reclaim=False):
        return create_pair_code(
            self.context, self.dispatcher, name, provider, room,
            display_name, reclaim,
            pair_providers=hub.PAIR_PROVIDERS,
            code_ttl=hub.PAIR_CODE_TTL,
            token_factory=lambda: f"pk-pair-{name}",
            hash_token=hub.hash_token,
            external_enabled=hub._external_enabled,
        )

    def test_create_pair_code_returns_one_time_plaintext_and_stores_hash(self):
        result = self.issue_code(display_name="Display")
        self.assertEqual(result["pair_code"], "pk-pair-new-external")
        self.assertEqual(result["member_name"], "new-external")
        self.assertEqual(result["display_name"], "Display")
        row = self.db.execute(
            "SELECT code_hash,member_name,provider,expires_at FROM pair_codes "
            "WHERE id=?", (result["code_id"],),
        ).fetchone()
        self.assertEqual(row["code_hash"], hub.hash_token("pk-pair-new-external"))
        self.assertNotEqual(row["code_hash"], "pk-pair-generated")
        self.assertEqual(row["member_name"], "new-external")
        self.assertEqual(row["expires_at"], 12500.0 + hub.PAIR_CODE_TTL)

    def test_create_pair_code_validates_identity_provider_room_and_display(self):
        for name, provider, room, display, expected in (
            ("bad name", "generic", "pack-main", "", "member_name"),
            ("ok", "invalid", "pack-main", "", "provider must"),
            ("ok", "generic", "missing-room", "", "no such room"),
            ("ok", "generic", "pack-main", "x" * 81, "display_name too long"),
        ):
            with self.assertRaisesRegex(ValueError, expected):
                self.issue_code(name, provider, room, display)
        self.assertEqual(self.count("pair_codes"), 1)

    def test_create_pair_code_enforces_reclaim_target_and_room(self):
        with self.assertRaisesRegex(ValueError, "reclaim target"):
            self.issue_code(name="missing-reclaim", reclaim=True)
        self.db.execute(
            "INSERT INTO members(name,role,token_hash,created_at,kind,bound_room_id) "
            "VALUES('internal','member','h',12000,'internal',1)"
        )
        self.db.execute(
            "INSERT INTO members(name,role,token_hash,created_at,kind,bound_room_id) "
            "VALUES('external','member','h2',12000,'external',1)"
        )
        self.db.execute(
            "INSERT INTO rooms(name,created_at) VALUES('other-room',12000)"
        )
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "internal member"):
            self.issue_code(name="internal")
        with self.assertRaisesRegex(ValueError, "use reclaim=True"):
            self.issue_code(name="external")
        self.assertEqual(self.issue_code(
            name="external", reclaim=True,
        )["reclaim"], True)
        with self.assertRaisesRegex(ValueError, "reclaim room mismatch"):
            self.issue_code(
                name="external", room="other-room", reclaim=True,
            )

    def test_create_pair_code_respects_external_gate_and_audits(self):
        self.db.execute(
            "INSERT INTO hub_settings(key,value) VALUES('external_enabled','0') "
            "ON CONFLICT(key) DO UPDATE SET value='0'"
        )
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "external access is disabled"):
            self.issue_code()
        self.assertEqual(self.count("pair_codes"), 1)
        self.assertEqual(self.count("audit", "WHERE action='pair_code_gate_blocked'"), 1)

    def test_pair_code_list_reports_status_without_hash_or_plaintext(self):
        result = self.issue_code()
        rows = list_pair_codes(self.context)["pair_codes"]
        row = next(x for x in rows if x["code_id"] == result["code_id"])
        self.assertEqual(row["status"], "unused")
        self.assertNotIn("code_hash", row)
        self.assertNotIn("pair_code", row)
        self.db.execute(
            "UPDATE pair_codes SET locked_at=12499 WHERE id=?",
            (result["code_id"],),
        )
        self.db.commit()
        self.assertEqual(
            next(x for x in list_pair_codes(self.context)["pair_codes"]
                 if x["code_id"] == result["code_id"])["status"],
            "locked",
        )

    def test_revoke_pair_code_and_reject_repeated_or_redeemed(self):
        result = self.issue_code()
        self.assertEqual(
            revoke_pair_code(self.context, self.dispatcher, result["code_id"]),
            {"code_id": result["code_id"], "revoked": True},
        )
        with self.assertRaisesRegex(ValueError, "already revoked"):
            revoke_pair_code(self.context, self.dispatcher, result["code_id"])
        second = self.issue_code(name="second")
        self.db.execute(
            "UPDATE pair_codes SET redeemed_at=12499 WHERE id=?",
            (second["code_id"],),
        )
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "already redeemed"):
            revoke_pair_code(self.context, self.dispatcher, second["code_id"])
        with self.assertRaisesRegex(ValueError, "no such pair code"):
            revoke_pair_code(self.context, self.dispatcher, 999999)

    def test_external_gate_read_toggle_and_audit(self):
        self.assertEqual(
            set_external_gate(
                self.context, self.dispatcher, None, "",
                external_enabled=hub._external_enabled,
            ),
            {"external_enabled": True},
        )
        self.assertEqual(
            set_external_gate(
                self.context, self.dispatcher, False, "incident",
                external_enabled=hub._external_enabled,
            ),
            {"external_enabled": False},
        )
        self.assertEqual(
            set_external_gate(
                self.context, self.dispatcher, None, "",
                external_enabled=hub._external_enabled,
            ),
            {"external_enabled": False},
        )
        set_external_gate(
            self.context, self.dispatcher, True, "resolved",
            external_enabled=hub._external_enabled,
        )
        self.assertEqual(self.count("audit", "WHERE action='external_gate_set'"), 2)

    def test_pair_code_create_audit_failure_rolls_back_code(self):
        self.db.execute(
            "CREATE TRIGGER reject_pair_code_create BEFORE INSERT ON audit "
            "WHEN NEW.action='pair_code_create' BEGIN "
            "SELECT RAISE(ABORT,'pair code audit rejected'); END"
        )
        self.db.commit()
        with self.assertRaisesRegex(Exception, "pair code audit rejected"):
            self.issue_code()
        self.assertEqual(self.count("pair_codes"), 1)

    def test_pair_code_revoke_audit_failure_preserves_unused_code(self):
        result = self.issue_code()
        self.db.execute(
            "CREATE TRIGGER reject_pair_code_revoke BEFORE INSERT ON audit "
            "WHEN NEW.action='pair_code_revoke' BEGIN "
            "SELECT RAISE(ABORT,'pair revoke audit rejected'); END"
        )
        self.db.commit()
        with self.assertRaisesRegex(Exception, "pair revoke audit rejected"):
            revoke_pair_code(self.context, self.dispatcher, result["code_id"])
        self.assertIsNone(self.db.execute(
            "SELECT revoked_at FROM pair_codes WHERE id=?",
            (result["code_id"],),
        ).fetchone()[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
