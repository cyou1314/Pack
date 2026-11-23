#!/usr/bin/env python3
"""Pair redemption service state, reclaim, throttling and race tests."""
import json
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.pairing import redeem_pair


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
