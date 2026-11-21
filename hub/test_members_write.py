#!/usr/bin/env python3
"""Member management tests with an isolated database and audit ledger."""
import tempfile
import threading
import unittest
from pathlib import Path

import hub
from core.context import HubContext
from services.members import rename_member, rotate_member_token, set_access_mode, set_login_state, set_member_quota, report_member_quota, remove_member


class MemberWriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-member-write-")
        root = Path(self.temp.name)
        self.context = HubContext(
            base_dir=str(root), db_path=str(root / "member.db"),
            attachment_dir=str(root / "attachments"),
            lock=threading.Lock(), clock=lambda: 9000.0,
        )
        hub.init(self.context)
        self.actor = {"name": "dispatcher"}
        self.db = self.context.connect()
        self.db.execute(
            "INSERT INTO members(name,role,token_hash,created_at,provider) "
            "VALUES('worker','member','dummy-hash',9000,'codex')"
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_access_mode_blocks_privileged_accounts_and_preserves_previous_value(self):
        self.assertEqual(
            set_access_mode(self.context,self.actor,"worker","readonly",hub.ACCESS_MODES),
            {"name":"worker","access_mode":"readonly"},
        )
        with self.assertRaisesRegex(ValueError,"bad access mode"):
            set_access_mode(self.context,self.actor,"worker","invalid",hub.ACCESS_MODES)
        with self.assertRaisesRegex(ValueError,"dispatcher"):
            set_access_mode(self.context,self.actor,"dispatcher","readonly",hub.ACCESS_MODES)
        with self.assertRaisesRegex(ValueError,"no such member"):
            set_access_mode(self.context,self.actor,"missing","full",hub.ACCESS_MODES)
        self.assertEqual(
            self.db.execute("SELECT access_mode FROM members WHERE name='worker'").fetchone()[0],
            "readonly",
        )

    def test_login_state_rejects_invalid_and_removed_members(self):
        result = set_login_state(
            self.context,self.actor,"worker","logged_in",hub.LOGIN_STATES
        )
        self.assertEqual(result, {"name":"worker","provider":"codex","login_state":"logged_in"})
        with self.assertRaisesRegex(ValueError,"bad login_state"):
            set_login_state(self.context,self.actor,"worker","unknown",hub.LOGIN_STATES)
        self.db.execute("UPDATE members SET removed_at=9001 WHERE name='worker'")
        self.db.commit()
        with self.assertRaisesRegex(ValueError,"member removed"):
            set_login_state(self.context,self.actor,"worker","expired",hub.LOGIN_STATES)
        self.assertEqual(
            self.db.execute("SELECT login_state FROM members WHERE name='worker'").fetchone()[0],
            "logged_in",
        )

    def test_rename_changes_display_only_and_records_audit(self):
        result=rename_member(self.context,self.actor,"worker","  New Worker  ")
        self.assertEqual(result,{"name":"worker","display_name":"New Worker"})
        row=self.db.execute(
            "SELECT name,display_name FROM members WHERE name='worker'"
        ).fetchone()
        self.assertEqual(tuple(row),("worker","New Worker"))
        with self.assertRaisesRegex(ValueError,"1-80 chars"):
            rename_member(self.context,self.actor,"worker"," ")
        with self.assertRaisesRegex(ValueError,"no such member"):
            rename_member(self.context,self.actor,"missing","Other")
        audit=self.db.execute(
            "SELECT action,detail FROM audit WHERE action='member_rename' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(tuple(audit),("member_rename","worker -> New Worker"))


    def test_token_rotation_invalidates_previous_hash_and_never_audits_secret(self):
        original_hash = self.db.execute(
            "SELECT token_hash FROM members WHERE name='worker'"
        ).fetchone()[0]
        generated = "pk-mem-test-only-secret"
        result = rotate_member_token(
            self.context, self.actor, "worker",
            token_factory=lambda: generated, hash_token=hub.hash_token,
        )
        self.assertEqual(result, {"name": "worker", "token": generated})
        current_hash = self.db.execute(
            "SELECT token_hash FROM members WHERE name='worker'"
        ).fetchone()[0]
        self.assertNotEqual(current_hash, original_hash)
        self.assertEqual(current_hash, hub.hash_token(generated))
        record = self.db.execute(
            "SELECT actor,detail FROM audit WHERE action='member_token_rotate'"
        ).fetchone()
        self.assertEqual(tuple(record), ("dispatcher", "worker"))
        self.assertNotIn(generated, record["detail"])

    def test_dispatcher_token_and_removed_member_cannot_be_rotated(self):
        token_path = Path(self.context.base_dir) / ".dispatcher_token"
        original_token = token_path.read_bytes()
        dispatcher_hash = self.db.execute(
            "SELECT token_hash FROM members WHERE role='dispatcher'"
        ).fetchone()[0]
        token_factory = lambda: "pk-mem-test-only-secret"
        with self.assertRaisesRegex(ValueError, "dispatcher"):
            rotate_member_token(
                self.context, self.actor, "dispatcher",
                token_factory=token_factory, hash_token=hub.hash_token,
            )
        with self.assertRaisesRegex(ValueError, "no such member"):
            rotate_member_token(
                self.context, self.actor, "missing",
                token_factory=token_factory, hash_token=hub.hash_token,
            )
        self.db.execute("UPDATE members SET removed_at=9001 WHERE name='worker'")
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "member removed"):
            rotate_member_token(
                self.context, self.actor, "worker",
                token_factory=token_factory, hash_token=hub.hash_token,
            )
        self.assertEqual(token_path.read_bytes(), original_token)
        self.assertEqual(
            self.db.execute(
                "SELECT token_hash FROM members WHERE role='dispatcher'"
            ).fetchone()[0], dispatcher_hash,
        )
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM audit WHERE action='member_token_rotate'"
            ).fetchone()[0], 0,
        )


    def test_dispatcher_quota_only_updates_specified_fields_with_audit(self):
        result = set_member_quota(
            self.context, self.actor, "worker", 42.5, None, 300,
            apply_quota=lambda conn, mid, used, reset, extra, source:
                hub._apply_quota(conn, mid, used, reset, extra, source, self.context),
        )
        self.assertEqual(result["name"], "worker")
        self.assertEqual(result["quota_week_used_pct"], 42.5)
        self.assertEqual(result["quota_extra_left"], 300)
        self.assertIsNone(result["quota_week_reset_at"])
        self.assertEqual(result["quota_source"], "dispatcher")
        self.assertEqual(result["quota_updated_at"], 9000.0)
        record = self.db.execute(
            "SELECT actor, detail FROM audit WHERE action='member_quota_set'"
        ).fetchone()
        self.assertEqual(record["actor"], "dispatcher")
        self.assertIn("quota_week_used_pct", record["detail"])
        self.assertIn("quota_extra_left", record["detail"])

    def test_quota_invalid_and_removed_member_leave_database_unchanged(self):
        def apply(conn, mid, used, reset, extra, source):
            return hub._apply_quota(conn, mid, used, reset, extra, source, self.context)
        for kwargs, message in [
            ({}, "nothing to update"),
            ({"week_used_pct": 101}, "out of range"),
            ({"week_reset_at": -1}, "epoch seconds"),
            ({"extra_tokens_left": -1}, "extra_tokens_left"),
        ]:
            with self.assertRaisesRegex(ValueError, message):
                set_member_quota(
                    self.context, self.actor, "worker", apply_quota=apply, **kwargs
                )
        with self.assertRaisesRegex(ValueError, "no such member"):
            set_member_quota(
                self.context, self.actor, "missing", week_used_pct=1, apply_quota=apply
            )
        self.db.execute("UPDATE members SET removed_at=9001 WHERE name='worker'")
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "member is removed"):
            set_member_quota(
                self.context, self.actor, "worker", week_used_pct=1, apply_quota=apply
            )
        self.assertIsNone(self.db.execute(
            "SELECT quota_week_used_pct FROM members WHERE name='worker'"
        ).fetchone()[0])
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM audit WHERE action='member_quota_set'"
        ).fetchone()[0], 0)


    def _report(self, pct=None, reset=None, extra=None):
        member = self.db.execute("SELECT id,name FROM members WHERE name='worker'").fetchone()
        return report_member_quota(
            self.context, dict(member), pct, reset, extra,
            validate_report=lambda a,b,c: hub._validate_quota_report(a,b,c,self.context),
            apply_quota=lambda conn,mid,a,b,c,source:
                hub._apply_quota(conn,mid,a,b,c,source,self.context),
            quota_anomaly_jump=hub.QUOTA_ANOMALY_JUMP,
        )

    def test_self_report_blocks_decrease_and_preserves_audit(self):
        self.db.execute(
            "UPDATE members SET quota_week_used_pct=80,quota_week_reset_at=12000,"
            "quota_source='fact' WHERE name='worker'"
        )
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "quota report blocked"):
            self._report(pct=20)
        row=self.db.execute(
            "SELECT quota_week_used_pct,quota_source FROM members WHERE name='worker'"
        ).fetchone()
        self.assertEqual(tuple(row),(80,"fact"))
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM audit WHERE action='member_quota_drop_blocked'"
        ).fetchone()[0],1)
        partial=self._report(pct=20,extra=30)
        self.assertEqual(partial["quota_week_used_pct"],80)
        self.assertEqual(partial["quota_extra_left"],30)
        self.assertIn("week_used_pct",partial["blocked_fields"])

    def test_self_report_reset_rollback_and_cycle_rollover(self):
        self.db.execute(
            "UPDATE members SET quota_week_used_pct=90,quota_week_reset_at=8500,"
            "quota_source='dispatcher' WHERE name='worker'"
        )
        self.db.commit()
        accepted=self._report(pct=10,reset=9300)
        self.assertEqual(accepted["quota_week_used_pct"],10)
        self.assertEqual(accepted["quota_week_reset_at"],9300)
        with self.assertRaisesRegex(ValueError,"quota report blocked"):
            self._report(reset=9200)
        self.assertEqual(self.db.execute(
            "SELECT quota_week_reset_at FROM members WHERE name='worker'"
        ).fetchone()[0],9300)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM audit WHERE action='member_quota_reset_blocked'"
        ).fetchone()[0],1)

    def test_self_report_spike_and_invalid_input(self):
        self.db.execute(
            "UPDATE members SET quota_week_used_pct=10,quota_source='self' "
            "WHERE name='worker'"
        )
        self.db.commit()
        result=self._report(pct=55)
        self.assertEqual(result["quota_week_used_pct"],55)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM audit WHERE action='quota_anomaly'"
        ).fetchone()[0],1)
        with self.assertRaisesRegex(ValueError,"invalid quota report"):
            self._report(pct=101)
        with self.assertRaisesRegex(ValueError,"invalid quota report"):
            self._report(reset=9000 + 86400 * 50)
        self.assertEqual(self.db.execute(
            "SELECT quota_week_used_pct FROM members WHERE name='worker'"
        ).fetchone()[0],55)


    def _remove(self, name, actor=None):
        return remove_member(
            self.context, actor or self.actor, name,
            close_run=lambda conn, tid, outcome, who:
                hub._close_run(conn, tid, outcome, who, context=self.context),
            desktop_release_for_task=lambda conn, tid, who, reason:
                hub._desktop_release_for_task(conn, tid, who, reason, self.context),
            desktop_snapshot=hub._desktop_snapshot,
            hash_token=hub.hash_token,
            token_factory=lambda: "removal-test-token",
        )

    def test_remove_member_reclaims_claimed_and_open_work_and_revokes_token(self):
        worker = self.db.execute(
            "SELECT id, token_hash FROM members WHERE name='worker'"
        ).fetchone()
        before_hash = worker["token_hash"]
        self.db.execute(
            "INSERT INTO tasks(room_id,title,body,created_by,assignee,status,"
            "claim_token,lease_expires,created_at,updated_at) "
            "VALUES(1,'claim-test','',?,?, 'claimed','claim-token',9100,8900,8900)",
            (worker["id"], worker["id"]),
        )
        self.db.execute(
            "INSERT INTO tasks(room_id,title,body,created_by,assignee,status,"
            "created_at,updated_at) VALUES(1,'open-test','',?,?, 'open',8900,8900)",
            (worker["id"], worker["id"]),
        )
        self.db.commit()
        result = self._remove("worker")
        self.assertEqual(result["removed"], "worker")
        self.assertEqual(result["tasks_recalled"], 1)
        self.assertEqual(result["tasks_unassigned"], 1)
        rows = self.db.execute(
            "SELECT status,assignee,claim_token FROM tasks ORDER BY id"
        ).fetchall()
        self.assertEqual(
            [tuple(r) for r in rows],
            [("open", None, None), ("open", None, None)],
        )
        after = self.db.execute(
            "SELECT removed_at,token_hash FROM members WHERE name='worker'"
        ).fetchone()
        self.assertEqual(after["removed_at"], 9000.0)
        self.assertNotEqual(after["token_hash"], before_hash)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM audit WHERE action='member_remove'"
        ).fetchone()[0], 1)

    def test_remove_rejects_self_dispatcher_owner_and_repeated_removal(self):
        self.db.execute(
            "INSERT INTO members(name,role,token_hash,created_at) "
            "VALUES('owner','owner','owner-test-hash',9000)"
        )
        self.db.commit()
        for name, actor, error in [
            ("dispatcher", {"name": "dispatcher"}, "cannot remove yourself"),
            ("dispatcher", {"name": "owner"}, "cannot remove the dispatcher"),
            ("owner", {"name": "dispatcher"}, "cannot remove the owner"),
            ("unknown", self.actor, "no such member"),
        ]:
            with self.assertRaisesRegex(ValueError, error):
                self._remove(name, actor=actor)
        self._remove("worker")
        with self.assertRaisesRegex(ValueError, "already removed"):
            self._remove("worker")


    def test_remove_member_expires_transfers_clears_skills_and_releases_desktop(self):
        worker_id = self.db.execute(
            "SELECT id FROM members WHERE name='worker'"
        ).fetchone()[0]
        self.db.execute(
            "INSERT INTO tasks(room_id,title,body,created_by,assignee,status,"
            "created_at,updated_at) VALUES(1,'transfer-test','',?,?, 'open',8900,8900)",
            (worker_id, worker_id),
        )
        task_id = self.db.execute(
            "SELECT id FROM tasks WHERE title='transfer-test'"
        ).fetchone()[0]
        self.db.execute(
            "INSERT INTO task_transfers(task_id,from_member,to_member,reason,actor,"
            "created_at,status) VALUES(?,'worker','dispatcher','handoff','worker',8900,'pending')",
            (task_id,),
        )
        self.db.execute(
            "INSERT INTO skills(name,description,current_version,updated_at)"
            " VALUES('fixture-skill','',0,8900)"
        )
        skill_id = self.db.execute(
            "SELECT id FROM skills WHERE name='fixture-skill'"
        ).fetchone()[0]
        self.db.execute(
            "INSERT INTO member_skills(member_id,skill_id) VALUES(?,?)",
            (worker_id, skill_id),
        )
        self.db.execute(
            "INSERT INTO desktop_lock(id,holder,task_id,acquired_at)"
            " VALUES(1,'worker',NULL,8900)"
            " ON CONFLICT(id) DO UPDATE SET holder=excluded.holder,"
            " task_id=excluded.task_id,acquired_at=excluded.acquired_at"
        )
        self.db.commit()
        result = self._remove("worker")
        self.assertEqual(result["transfers_expired"], 1)
        self.assertEqual(result["skills_cleared"], 1)
        self.assertTrue(result["lock_released"])
        self.assertEqual(
            self.db.execute("SELECT status FROM task_transfers").fetchone()[0],
            "expired",
        )
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM member_skills").fetchone()[0], 0
        )
        self.assertIsNone(
            self.db.execute(
                "SELECT holder FROM desktop_lock WHERE id=1"
            ).fetchone()[0]
        )
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM audit WHERE action='desktop_auto_release'"
            ).fetchone()[0], 1
        )


    def test_remove_rolls_back_all_related_tables_when_token_revocation_fails(self):
        """A late failure must roll back tasks, transfers, skills, locks and audit."""
        worker = self.db.execute(
            "SELECT id, token_hash FROM members WHERE name='worker'"
        ).fetchone()
        self.db.execute(
            "INSERT INTO tasks(room_id,title,body,created_by,assignee,status,"
            "claim_token,lease_expires,created_at,updated_at) "
            "VALUES(1,'rollback-claim','',?,?,'claimed','lease',9999,8900,8900)",
            (worker["id"], worker["id"]),
        )
        claim_id = self.db.execute(
            "SELECT id FROM tasks WHERE title='rollback-claim'"
        ).fetchone()[0]
        self.db.execute(
            "INSERT INTO tasks(room_id,title,body,created_by,assignee,status,"
            "created_at,updated_at) "
            "VALUES(1,'rollback-open','',?,?,'open',8900,8900)",
            (worker["id"], worker["id"]),
        )
        self.db.execute(
            "INSERT INTO task_transfers(task_id,from_member,to_member,reason,actor,"
            "created_at,status) VALUES(?,'worker','dispatcher','test','worker',8900,'pending')",
            (claim_id,),
        )
        self.db.execute(
            "INSERT INTO skills(name,description,current_version,updated_at)"
            " VALUES('rollback-skill','',0,8900)"
        )
        skill_id = self.db.execute(
            "SELECT id FROM skills WHERE name='rollback-skill'"
        ).fetchone()[0]
        self.db.execute(
            "INSERT INTO member_skills(member_id,skill_id) VALUES(?,?)",
            (worker["id"], skill_id),
        )
        self.db.execute(
            "INSERT INTO desktop_lock(id,holder,task_id,acquired_at)"
            " VALUES(1,'worker',NULL,8900)"
            " ON CONFLICT(id) DO UPDATE SET holder=excluded.holder,"
            " task_id=excluded.task_id,acquired_at=excluded.acquired_at"
        )
        self.db.commit()

        def refuse_hash(_):
            raise RuntimeError("injected token revocation failure")

        with self.assertRaisesRegex(RuntimeError, "injected token revocation"):
            remove_member(
                self.context, self.actor, "worker",
                close_run=lambda conn, tid, outcome, who:
                    hub._close_run(conn, tid, outcome, who, context=self.context),
                desktop_release_for_task=lambda conn, tid, who, reason:
                    hub._desktop_release_for_task(conn, tid, who, reason, self.context),
                desktop_snapshot=hub._desktop_snapshot,
                hash_token=refuse_hash,
                token_factory=lambda: "no-real-secret",
            )

        self.assertEqual(
            [tuple(row) for row in self.db.execute(
                "SELECT status,assignee,claim_token FROM tasks ORDER BY id"
            )],
            [("claimed", worker["id"], "lease"), ("open", worker["id"], None)],
        )
        self.assertEqual(
            self.db.execute("SELECT status FROM task_transfers").fetchone()[0],
            "pending",
        )
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM member_skills").fetchone()[0], 1
        )
        self.assertEqual(
            self.db.execute("SELECT holder FROM desktop_lock WHERE id=1").fetchone()[0],
            "worker",
        )
        member = self.db.execute(
            "SELECT removed_at,token_hash FROM members WHERE name='worker'"
        ).fetchone()
        self.assertIsNone(member["removed_at"])
        self.assertEqual(member["token_hash"], worker["token_hash"])
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM audit WHERE action IN "
                "('member_remove','desktop_auto_release','run_close')"
            ).fetchone()[0], 0,
        )

    def test_concurrent_remove_serializes_and_revokes_only_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        start = Barrier(3, timeout=10)

        def attempt():
            start.wait()
            try:
                return ("success", self._remove("worker"))
            except ValueError as error:
                return ("rejected", str(error))

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(attempt)
            b = pool.submit(attempt)
            start.wait()
            results = [a.result(timeout=20), b.result(timeout=20)]

        self.assertEqual(
            sorted(kind for kind, _ in results), ["rejected", "success"]
        )
        rejected = next(detail for kind, detail in results if kind == "rejected")
        self.assertIn("already removed", rejected)
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM audit WHERE action='member_remove'"
            ).fetchone()[0], 1,
        )
        self.assertEqual(
            self.db.execute(
                "SELECT COUNT(*) FROM members WHERE name='worker' AND removed_at IS NOT NULL"
            ).fetchone()[0], 1,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
