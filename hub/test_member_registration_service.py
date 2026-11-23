#!/usr/bin/env python3
"""Member registration and heartbeat service tests."""
import json
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.members import heartbeat, register_member


class MemberRegistrationServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-member-register-")
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
        self.db.execute(
            "INSERT INTO members(name,role,token_hash,created_at,removed_at) "
            "VALUES('reserved','member','old-hash',12000,12400)"
        )
        self.db.commit()
        used = hub._used_combos(self.db)
        available = next(
            ({
                "species": species, "palette": palette,
                "accessory": accessory, "bg": background,
            }
             for species in hub.AVATAR_SPECIES
             for palette in hub.AVATAR_PALETTES
             for accessory in hub.AVATAR_ACCESSORIES
             for background in hub.AVATAR_BGS
             if f"{species}|{palette}|{accessory}|{background}" not in used),
            None,
        )
        self.assertIsNotNone(available)
        self.combo = json.dumps(available)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def register(self, context=None, name="alice", profile="", avatar_combo="",
                 provider=""):
        ctx = context or self.context
        return register_member(
            ctx, self.dispatcher, name, profile, avatar_combo, provider,
            token_factory=lambda: "pk-mem-test-token",
            hash_token=hub.hash_token,
            avatar_for=hub.avatar_for,
            used_combos=hub._used_combos,
            make_avatar_combo=hub._avatar_combo,
            avatar_species=hub.AVATAR_SPECIES,
            avatar_palettes=hub.AVATAR_PALETTES,
            avatar_accessories=hub.AVATAR_ACCESSORIES,
            avatar_bgs=hub.AVATAR_BGS,
        )

    def count(self, table, where="", args=()):
        return self.db.execute(
            f"SELECT COUNT(*) FROM {table} {where}", args,
        ).fetchone()[0]

    def test_register_creates_hashed_token_and_unique_avatar(self):
        result = self.register(profile="profile")
        self.assertGreater(result["member_id"], 1)
        self.assertEqual(result["name"], "alice")
        self.assertTrue(result["token"].startswith("pk-mem-"))
        self.assertIsNone(result["provider"])
        self.assertIsNone(result["login_state"])
        row = self.db.execute(
            "SELECT token_hash,profile,avatar FROM members WHERE name='alice'"
        ).fetchone()
        self.assertEqual(row["token_hash"], hub.hash_token(result["token"]))
        self.assertEqual(row["profile"], "profile")
        avatar = json.loads(row["avatar"])
        self.assertIn("combo", avatar)
        self.assertEqual(result["avatar"], avatar)

    def test_provider_starts_login_pending(self):
        result = self.register(provider="muse")
        self.assertEqual(result["provider"], "muse")
        self.assertEqual(result["login_state"], "pending")
        row = self.db.execute(
            "SELECT provider,login_state FROM members WHERE name='alice'"
        ).fetchone()
        self.assertEqual(tuple(row), ("muse", "pending"))

    def test_custom_avatar_combo_is_applied(self):
        result = self.register(avatar_combo=self.combo)
        self.assertEqual(
            {key: result["avatar"][key] for key in
             ("species", "palette", "accessory", "bg")},
            {"species": "fox", "palette": "ocean", "accessory": "cap", "bg": "peach"},
        )
        self.assertEqual(result["avatar"]["combo"], "fox|ocean|cap|peach")

    def test_invalid_avatar_json_shape_and_fields_are_rejected(self):
        for value, expected in (
            ("{", "not valid JSON"),
            ("[]", "must be a JSON object"),
            (json.dumps({"species": "dragon"}), "bad species"),
        ):
            with self.assertRaisesRegex(ValueError, expected):
                self.register(avatar_combo=value)
        self.assertEqual(self.count("members", "WHERE name='alice'"), 0)

    def test_duplicate_avatar_combo_is_rejected(self):
        self.register(name="alice", avatar_combo=self.combo)
        with self.assertRaisesRegex(ValueError, "combo already taken"):
            self.register(name="bob", avatar_combo=self.combo)
        self.assertEqual(self.count("members", "WHERE name='bob'"), 0)

    def test_duplicate_active_and_removed_names_are_rejected(self):
        self.register(name="alice")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.register(name="alice")
        with self.assertRaisesRegex(ValueError, "reserved by removed member"):
            self.register(name="reserved")
        self.assertEqual(self.count("members", "WHERE name='reserved'"), 1)

    def test_profile_and_provider_limits(self):
        with self.assertRaisesRegex(ValueError, "profile too long"):
            self.register(profile="x" * 2001)
        with self.assertRaisesRegex(ValueError, "provider too long"):
            self.register(provider="p" * 65)
        self.assertEqual(self.count("members", "WHERE name='alice'"), 0)

    def test_audit_failure_rolls_back_registration(self):
        self.db.execute(
            "CREATE TRIGGER reject_member_register BEFORE INSERT ON audit "
            "WHEN NEW.action='member_register' BEGIN "
            "SELECT RAISE(ABORT,'member register audit rejected'); END"
        )
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "member name already exists"):
            self.register()
        self.assertEqual(self.count("members", "WHERE name='alice'"), 0)

    def test_independent_contexts_serialize_avatar_combo_allocation(self):
        root = Path(self.temp.name)
        independent = HubContext(
            base_dir=str(root), db_path=str(root / "hub.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(), clock=lambda: 12500.0,
        )
        warm = independent.connect()
        warm.close()
        barrier = threading.Barrier(2)
        successes, errors = [], []

        def submit(context, name):
            try:
                barrier.wait(timeout=10)
                successes.append(self.register(
                    context=context, name=name, avatar_combo=self.combo,
                ))
            except Exception as error:
                errors.append(error)

        threads = [
            threading.Thread(target=submit, args=(self.context, "alice")),
            threading.Thread(target=submit, args=(independent, "bob")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("combo already taken", str(errors[0]))
        self.assertEqual(
            self.count("members", "WHERE name IN ('alice','bob')"), 1,
        )

    def test_heartbeat_updates_timestamp_without_audit(self):
        self.db.execute(
            "INSERT INTO members(name,role,token_hash,created_at) "
            "VALUES('alice','member','hash',12000)"
        )
        self.db.commit()
        actor = dict(self.db.execute(
            "SELECT * FROM members WHERE name='alice'"
        ).fetchone())
        self.assertEqual(heartbeat(self.context, actor), {
            "ok": True, "member": "alice",
        })
        self.assertEqual(self.db.execute(
            "SELECT last_heartbeat FROM members WHERE id=?", (actor["id"],),
        ).fetchone()[0], 12500.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
