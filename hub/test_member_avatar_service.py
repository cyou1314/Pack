#!/usr/bin/env python3
"""Member avatar uniqueness and audit transaction tests."""
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.members import set_member_avatar


class MemberAvatarServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-member-avatar-")
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
        used = hub._used_combos(self.db)
        self.initial = {}
        for name in ("alice", "bob"):
            avatar = hub.avatar_for(name)
            combo = hub._avatar_combo(name, used)
            used.add(combo["combo"])
            avatar.update(combo)
            cursor = self.db.execute(
                "INSERT INTO members(name,role,token_hash,created_at,avatar) "
                "VALUES(?,'member',?,12000,?)",
                (name, f"{name}-hash", json.dumps(avatar)),
            )
            self.initial[name] = avatar
        self.db.execute(
            "INSERT INTO members(name,role,token_hash,created_at,removed_at) "
            "VALUES('removed','member','removed-hash',12000,12400)"
        )
        self.db.commit()
        used = hub._used_combos(self.db)
        self.available = next(
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
        self.assertIsNotNone(self.available)
        self.combo = json.dumps(self.available)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def set_avatar(self, context=None, name="alice", combo=None):
        return set_member_avatar(
            context or self.context, self.dispatcher, name,
            self.combo if combo is None else combo,
            avatar_species=hub.AVATAR_SPECIES,
            avatar_palettes=hub.AVATAR_PALETTES,
            avatar_accessories=hub.AVATAR_ACCESSORIES,
            avatar_bgs=hub.AVATAR_BGS,
            used_combos=hub._used_combos,
        )

    def current(self, name):
        row = self.db.execute(
            "SELECT avatar FROM members WHERE name=?", (name,),
        ).fetchone()
        return json.loads(row["avatar"]) if row and row["avatar"] else {}

    def count_audit(self):
        return self.db.execute(
            "SELECT COUNT(*) FROM audit WHERE action='member_avatar_set'"
        ).fetchone()[0]

    def test_set_avatar_replaces_only_combo_fields(self):
        result = self.set_avatar()
        self.assertEqual(
            {key: result["avatar"][key] for key in
             ("species", "palette", "accessory", "bg")},
            self.available,
        )
        self.assertEqual(result["avatar"]["glyph"], self.initial["alice"]["glyph"])
        self.assertEqual(result["avatar"]["c1"], self.initial["alice"]["c1"])
        self.assertEqual(result["avatar"]["combo"], "|".join(
            self.available[key] for key in
            ("species", "palette", "accessory", "bg")
        ))
        self.assertEqual(self.count_audit(), 1)

    def test_same_combo_is_idempotently_accepted(self):
        avatar = self.initial["alice"]
        payload = json.dumps({
            key: avatar[key] for key in
            ("species", "palette", "accessory", "bg")
        })
        self.assertEqual(self.set_avatar(combo=payload)["avatar"]["combo"], avatar["combo"])
        self.assertEqual(self.count_audit(), 1)

    def test_invalid_json_shape_missing_and_out_of_range_fields(self):
        for payload, expected in (
            ("{", "not valid JSON"),
            ("[]", "must be a JSON object"),
            (json.dumps({"species": "fox"}), "missing palette"),
            (json.dumps({
                "species": "dragon", "palette": "ocean",
                "accessory": "cap", "bg": "peach",
            }), "bad species"),
        ):
            with self.assertRaisesRegex(ValueError, expected):
                self.set_avatar(combo=payload)
        self.assertEqual(self.count_audit(), 0)

    def test_combo_owned_by_another_member_is_rejected(self):
        avatar = self.initial["bob"]
        payload = json.dumps({
            key: avatar[key] for key in
            ("species", "palette", "accessory", "bg")
        })
        with self.assertRaisesRegex(ValueError, "combo already taken"):
            self.set_avatar(name="alice", combo=payload)
        self.assertEqual(self.count_audit(), 0)

    def test_missing_and_removed_members_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "no such member"):
            self.set_avatar(name="ghost")
        with self.assertRaisesRegex(ValueError, "member removed"):
            self.set_avatar(name="removed")
        self.assertEqual(self.count_audit(), 0)

    def test_audit_failure_rolls_back_avatar_change(self):
        before = self.current("alice")
        self.db.execute(
            "CREATE TRIGGER reject_avatar_audit BEFORE INSERT ON audit "
            "WHEN NEW.action='member_avatar_set' BEGIN "
            "SELECT RAISE(ABORT,'avatar audit rejected'); END"
        )
        self.db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "avatar audit rejected"):
            self.set_avatar()
        self.assertEqual(self.current("alice"), before)
        self.assertEqual(self.count_audit(), 0)

    def test_independent_contexts_cannot_assign_same_combo_to_two_members(self):
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
                successes.append(self.set_avatar(context=context, name=name))
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
        self.assertEqual(self.count_audit(), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
