#!/usr/bin/env python3
"""Non-destructive upgrade and rollback tests for the packaged macOS installer."""
import json
import os
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from io import StringIO

import manager


MARKER = "com.cyou.pack.installer.v1"


def write_text(path, value, mode=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)
    return path


def make_app(path, version, extra_file):
    path = Path(path)
    contents = path / "Contents"
    contents.mkdir(parents=True, exist_ok=True)
    info = {
        "CFBundleIdentifier": "com.cyou.pack",
        "CFBundleExecutable": "PackApp",
        "CFBundleShortVersionString": version,
        "PackInstallerMarker": MARKER,
    }
    with (contents / "Info.plist").open("wb") as stream:
        plistlib.dump(info, stream)
    write_text(path / extra_file, version)
    return path


class UpgradeFixture:
    def __init__(self, test):
        self.temp = tempfile.TemporaryDirectory(prefix="pack-upgrade-test-")
        test.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)

        self.home = root / "home"
        self.base = root / "pack"
        self.hub = self.base / "local-hub"
        self.engine = self.base / "local-engine"
        self.config = self.base / "config.json"
        self.socket_dir = self.base / "handoff-socket"
        self.agents = self.home / "Library" / "LaunchAgents"
        self.app_path = self.base / "Applications" / "Pack.app"
        self.bundle = root / "bundle"
        self.labels = {
            "hub": "com.pack.installer-test-hub",
            "watch": "com.pack.installer-test-watch",
            "backup": "com.pack.installer-test-backup",
            "engine": "com.pack.installer-test-engine",
        }
        self.uid = 501
        self.hp = 18795
        self.ep = 47822

        for directory in (
            self.home / "Library",
            self.agents,
            self.hub / "attachments",
            self.hub / "backups",
            self.engine / "data" / "local",
            self.socket_dir,
            self.app_path.parent,
        ):
            directory.mkdir(parents=True, exist_ok=True)

        write_text(self.config, json.dumps({"hubUrl": "http://127.0.0.1:18795/mcp"}), 0o600)
        write_text(self.hub / "pack.db", "original-db", 0o600)
        write_text(self.hub / ".dispatcher_token", "dispatcher-secret", 0o600)
        write_text(self.hub / ".owner_token", "owner-secret", 0o600)
        write_text(self.hub / "attachments" / "image.bin", "original-attachment")
        write_text(self.engine / "data" / "local" / "session.json", "original-session")

        # Existing program payload.
        write_text(self.hub / "python" / "bin" / "python3", "old-python", 0o755)
        write_text(self.hub / "hub.py", "old-hub")
        write_text(self.hub / "room_watch.py", "old-watch")
        write_text(self.hub / "backup-db.sh", "old-backup")
        write_text(self.hub / "verify-backup.sh", "old-verify")
        write_text(self.hub / "core" / "context.py", "old-context")
        write_text(self.hub / "services" / "rooms.py", "old-rooms")
        write_text(self.engine / "app" / "headless.cjs", "old-engine")
        write_text(self.engine / "Electron.app" / "Contents" / "MacOS" / "Electron", "old-electron")
        make_app(self.app_path, "1", "old-app.txt")

        # New DMG payload.
        write_text(self.bundle / "python" / "bin" / "python3", "new-python", 0o755)
        write_text(self.bundle / "hub" / "hub.py", "new-hub")
        write_text(self.bundle / "hub" / "room_watch.py", "new-watch")
        write_text(self.bundle / "hub" / "backup-db.sh", "new-backup")
        write_text(self.bundle / "hub" / "verify-backup.sh", "new-verify")
        write_text(self.bundle / "hub" / "core" / "context.py", "new-context")
        write_text(self.bundle / "hub" / "services" / "rooms.py", "new-rooms")
        write_text(self.bundle / "engine" / "app" / "headless.cjs", "new-engine")
        write_text(self.bundle / "engine" / "Electron.app" / "Contents" / "MacOS" / "Electron", "new-electron")
        make_app(self.bundle / "Pack.app", "2", "new-app.txt")

        self.plist_bytes = {}
        expected = {
            "hub": [str(self.hub / "python/bin/python3"), str(self.hub / "hub.py"), "serve"],
            "watch": [str(self.hub / "python/bin/python3"), str(self.hub / "room_watch.py")],
            "backup": ["/bin/bash", str(self.hub / "backup-db.sh")],
            "engine": [str(self.engine / "Electron.app/Contents/MacOS/Electron"), str(self.engine / "app")],
        }
        for kind, label in self.labels.items():
            path = self.agents / (label + ".plist")
            with path.open("wb") as stream:
                plistlib.dump({"Label": label, "ProgramArguments": expected[kind], "Umask": 0o077}, stream)
            self.plist_bytes[label] = path.read_bytes()

        self.patches = ExitStack()
        test.addCleanup(self.patches.close)
        for name, value in {
            "HOME": self.home,
            "base": self.base,
            "hub": self.hub,
            "engine": self.engine,
            "config": self.config,
            "socket_dir": self.socket_dir,
            "agents": self.agents,
            "app_path": self.app_path,
            "BUNDLE": self.bundle,
            "labels": self.labels,
            "uid": self.uid,
            "hp": self.hp,
            "ep": self.ep,
        }.items():
            self.patches.enter_context(patch.object(manager, name, value))

    def fake_ctl(self, fail_watch_once=False, mutate_db_on_hub_bootstrap=False, mutate_db_on_first_bootout=False, keep_hub_loaded_on_rollback=False):
        loaded = set(self.labels.values())
        calls = []
        state = {"fail_watch_once": fail_watch_once, "mutate_db": mutate_db_on_hub_bootstrap, "mutate_on_bootout": mutate_db_on_first_bootout, "bootout_counts": {}}

        def ctl(*args, check=True):
            args = tuple(str(value) for value in args)
            calls.append(args)
            if args[0] == "print":
                label = args[1].split("/")[-1]
                return SimpleNamespace(returncode=0 if label in loaded else 1)
            if args[0] == "bootout":
                label = args[1].split("/")[-1]
                state["bootout_counts"][label] = state["bootout_counts"].get(label, 0) + 1
                if state["mutate_on_bootout"]:
                    write_text(self.hub / "pack.db", "written-before-backup")
                    state["mutate_on_bootout"] = False
                if keep_hub_loaded_on_rollback and label.endswith("-hub") and state["bootout_counts"][label] > 1:
                    return SimpleNamespace(returncode=1)
                loaded.discard(label)
                return SimpleNamespace(returncode=0)
            if args[0] == "bootstrap":
                label = Path(args[2]).stem
                if state["mutate_db"] and label.endswith("-hub"):
                    write_text(self.hub / "pack.db", "mutated-by-new-hub")
                    write_text(self.hub / "pack.db-wal", "new-wal")
                    write_text(self.hub / "pack.db-shm", "new-shm")
                    state["mutate_db"] = False
                if state["fail_watch_once"] and label.endswith("-watch"):
                    state["fail_watch_once"] = False
                    raise subprocess.CalledProcessError(1, args)
                loaded.add(label)
                return SimpleNamespace(returncode=0)
            return SimpleNamespace(returncode=0)

        return ctl, calls, loaded

    def fake_subprocess_run(self, pgrep_returncode=1, backup_failure=False):
        calls = []

        def run(args, **kwargs):
            args = [str(value) for value in args]
            calls.append(args)
            if args[0] == "/usr/bin/pgrep":
                return SimpleNamespace(returncode=pgrep_returncode)
            if args[0] == "/bin/bash":
                script = Path(args[1]).name
                if script == "backup-db.sh":
                    if backup_failure:
                        raise subprocess.CalledProcessError(1, args)
                    backup_dir = Path(kwargs["env"]["PACK_BACKUP_DIR"])
                    backup_dir.mkdir(parents=True, exist_ok=True)
                    live_db = Path(kwargs["env"]["PACK_HUB_DIR"]) / "pack.db"
                    write_text(backup_dir / "pack-test.db", live_db.read_text(encoding="utf-8"))
                    write_text(backup_dir / "attachments-test.tar.gz", "archive")
                    write_text(backup_dir / "manifest-test.sha256", "manifest")
                return SimpleNamespace(returncode=0)
            if args[0] == "/usr/bin/ditto":
                shutil.copytree(Path(args[1]), Path(args[2]), symlinks=True)
                return SimpleNamespace(returncode=0)
            if args[0] in ("codesign", "plutil") or args[0].endswith("/python3"):
                return SimpleNamespace(returncode=0)
            raise AssertionError(f"Unexpected subprocess call: {args}")

        return run, calls


class UpgradeSafetyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = UpgradeFixture(self)

    def test_unowned_agent_is_rejected_before_backup_or_service_stop(self):
        f = self.fixture
        path = f.agents / (f.labels["watch"] + ".plist")
        with path.open("rb") as stream:
            document = plistlib.load(stream)
        document["ProgramArguments"][1] = str(f.base / "unrelated" / "room_watch.py")
        with path.open("wb") as stream:
            plistlib.dump(document, stream)

        ctl, calls, _ = f.fake_ctl()
        with patch.object(manager, "ctl", side_effect=ctl):
            with self.assertRaisesRegex(SystemExit, "outside the Pack service directory"):
                manager.upgrade()

        self.assertFalse(any(call[0] == "bootout" for call in calls))
        self.assertEqual(list((f.hub / "backups").glob("upgrade-*")), [])

    def test_running_app_is_rejected_before_backup_or_service_stop(self):
        f = self.fixture
        ctl, calls, _ = f.fake_ctl()
        run, _ = f.fake_subprocess_run(pgrep_returncode=0)
        with patch.object(manager, "ctl", side_effect=ctl), patch.object(manager.subprocess, "run", side_effect=run):
            with self.assertRaisesRegex(SystemExit, "quit Pack.app"):
                manager.upgrade()

        self.assertFalse(any(call[0] == "bootout" for call in calls))
        self.assertEqual(list((f.hub / "backups").glob("upgrade-*")), [])

    def test_backup_failure_restarts_services_without_changing_data(self):
        f = self.fixture
        ctl, calls, loaded = f.fake_ctl()
        run, _ = f.fake_subprocess_run(backup_failure=True)
        with (
            patch.object(manager, "ctl", side_effect=ctl),
            patch.object(manager.subprocess, "run", side_effect=run),
            patch.object(manager, "available", return_value=True),
            patch.object(manager, "wait_for_hub_ready", return_value=None),
            patch.object(manager, "wait_for_port", return_value=None),
        ):
            with self.assertRaisesRegex(RuntimeError, "Partial backup retained"):
                manager.upgrade()

        self.assertTrue(any(call[0] == "bootout" for call in calls))
        self.assertTrue(any(call[0] == "bootstrap" for call in calls))
        self.assertEqual(loaded, set(f.labels.values()))
        self.assertEqual((f.hub / "pack.db").read_text(encoding="utf-8"), "original-db")
        self.assertEqual((f.hub / "hub.py").read_text(encoding="utf-8"), "old-hub")
        self.assertTrue(list((f.hub / "backups").glob("upgrade-*")))

    def test_port_conflict_after_stop_does_not_rewind_live_database(self):
        f = self.fixture
        ctl, calls, loaded = f.fake_ctl(mutate_db_on_first_bootout=True)
        run, _ = f.fake_subprocess_run()
        with (
            patch.object(manager, "ctl", side_effect=ctl),
            patch.object(manager.subprocess, "run", side_effect=run),
            patch.object(manager, "available", return_value=False),
            patch.object(manager, "wait_for_hub_ready", return_value=None),
            patch.object(manager, "wait_for_port", return_value=None),
        ):
            with self.assertRaisesRegex(RuntimeError, "rollback needs attention"):
                manager.upgrade()

        self.assertEqual((f.hub / "pack.db").read_text(encoding="utf-8"), "written-before-backup")
        self.assertEqual((f.hub / "hub.py").read_text(encoding="utf-8"), "old-hub")
        self.assertEqual(loaded, set())
        self.assertFalse(any(call[0] == "bootstrap" for call in calls))
        self.assertEqual(list((f.hub / "backups").glob("upgrade-*")), [])
        self.assertTrue(list(f.base.glob(".pack-upgrade-*")))

    def test_successful_upgrade_preserves_data_and_retains_verified_backup(self):
        f = self.fixture
        ctl, calls, loaded = f.fake_ctl(mutate_db_on_first_bootout=True)
        run, subprocess_calls = f.fake_subprocess_run()
        output = StringIO()
        with (
            patch.object(manager, "ctl", side_effect=ctl),
            patch.object(manager.subprocess, "run", side_effect=run),
            patch.object(manager, "available", return_value=True),
            patch.object(manager, "wait_for_hub_ready") as hub_ready,
            patch.object(manager, "wait_for_port", return_value=None),
            redirect_stdout(output),
        ):
            manager.upgrade()

        self.assertEqual((f.hub / "hub.py").read_text(encoding="utf-8"), "new-hub")
        self.assertEqual((f.hub / "core" / "context.py").read_text(encoding="utf-8"), "new-context")
        self.assertEqual((f.engine / "app" / "headless.cjs").read_text(encoding="utf-8"), "new-engine")
        self.assertTrue((f.app_path / "new-app.txt").is_file())
        self.assertFalse((f.app_path / "old-app.txt").exists())

        self.assertEqual((f.hub / "pack.db").read_text(encoding="utf-8"), "written-before-backup")
        self.assertEqual((f.hub / "attachments" / "image.bin").read_text(encoding="utf-8"), "original-attachment")
        self.assertEqual((f.engine / "data" / "local" / "session.json").read_text(encoding="utf-8"), "original-session")
        self.assertEqual(f.config.read_text(encoding="utf-8"), json.dumps({"hubUrl": "http://127.0.0.1:18795/mcp"}))
        self.assertEqual((f.hub / ".dispatcher_token").read_text(encoding="utf-8"), "dispatcher-secret")
        for label, original in f.plist_bytes.items():
            self.assertEqual((f.agents / (label + ".plist")).read_bytes(), original)

        backups = list((f.hub / "backups").glob("upgrade-*"))
        self.assertEqual(len(backups), 1)
        backup = backups[0]
        self.assertEqual((backup / "database" / "pack-test.db").read_text(encoding="utf-8"), "written-before-backup")
        self.assertEqual((backup / "code" / "hub" / "hub.py").read_text(encoding="utf-8"), "old-hub")
        self.assertTrue((backup / "Pack.app" / "old-app.txt").is_file())
        self.assertEqual((backup / "state" / "config.json").stat().st_mode & 0o777, 0o600)
        self.assertIn("UPGRADE OK", output.getvalue())
        hub_ready.assert_called_once()
        self.assertEqual(loaded, set(f.labels.values()))
        self.assertTrue(any(args[0] == "/usr/bin/ditto" for args in subprocess_calls))
        self.assertTrue(any(call[0] == "bootout" for call in calls))
        self.assertTrue(any(call[0] == "bootstrap" for call in calls))

    def test_program_swap_rollback_failure_keeps_services_stopped(self):
        f = self.fixture
        ctl, calls, loaded = f.fake_ctl()
        run, _ = f.fake_subprocess_run()
        real_replace = os.replace
        failed = {"services_install": False}

        def guarded_replace(source, destination):
            source = Path(source)
            destination = Path(destination)
            if (
                destination == f.hub / "services"
                and ".pack-upgrade-" in str(source)
                and not failed["services_install"]
            ):
                failed["services_install"] = True
                raise OSError("injected staged services rename failure")
            if (
                destination == f.hub / "python"
                and "previous" in source.parts
            ):
                raise OSError("injected Python rollback rename failure")
            return real_replace(source, destination)

        with (
            patch.object(manager, "ctl", side_effect=ctl),
            patch.object(manager.subprocess, "run", side_effect=run),
            patch.object(manager, "available", return_value=True),
            patch.object(manager.os, "replace", side_effect=guarded_replace),
        ):
            with self.assertRaisesRegex(RuntimeError, "rollback needs attention"):
                manager.upgrade()

        self.assertTrue(failed["services_install"])
        self.assertFalse(any(call[0] == "bootstrap" for call in calls))
        self.assertEqual(loaded, set())
        self.assertTrue(list(f.base.glob(".pack-upgrade-*/previous/hub/python")))
        self.assertTrue((f.app_path / "old-app.txt").is_file())

    def test_app_bundle_rollback_failure_keeps_services_stopped(self):
        f = self.fixture
        ctl, calls, loaded = f.fake_ctl()
        run, _ = f.fake_subprocess_run()
        real_replace = os.replace
        failed = {"new_app_install": False}

        def guarded_replace(source, destination):
            source = Path(source)
            destination = Path(destination)
            if (
                destination == f.app_path
                and ".Pack.app.upgrade-" in str(source)
                and not failed["new_app_install"]
            ):
                failed["new_app_install"] = True
                raise OSError("injected new App rename failure")
            if (
                destination == f.app_path
                and ".Pack.app.previous-" in str(source)
            ):
                raise OSError("injected App rollback rename failure")
            return real_replace(source, destination)

        with (
            patch.object(manager, "ctl", side_effect=ctl),
            patch.object(manager.subprocess, "run", side_effect=run),
            patch.object(manager, "available", return_value=True),
            patch.object(manager.os, "replace", side_effect=guarded_replace),
        ):
            with self.assertRaisesRegex(RuntimeError, "rollback needs attention"):
                manager.upgrade()

        self.assertTrue(failed["new_app_install"])
        self.assertFalse(any(call[0] == "bootstrap" for call in calls))
        self.assertEqual(loaded, set())
        self.assertFalse(f.app_path.exists())
        self.assertEqual(len(list(f.app_path.parent.glob(".Pack.app.previous-*"))), 1)
        self.assertEqual((f.hub / "hub.py").read_text(encoding="utf-8"), "old-hub")

    def test_restore_refuses_a_backup_that_fails_reverification(self):
        f = self.fixture
        backup_root = f.hub / "backups" / "upgrade-reverify"
        database = backup_root / "database"
        state = backup_root / "state"
        old_code = backup_root / "code" / "hub"
        database.mkdir(parents=True)
        state.mkdir()
        old_code.mkdir(parents=True)
        write_text(database / "pack-test.db", "snapshot")
        write_text(old_code / "verify-backup.sh", "#!/bin/bash\nexit 0\n")

        def fail_verification(args, **kwargs):
            self.assertEqual(Path(args[1]).name, "verify-backup.sh")
            raise subprocess.CalledProcessError(1, args)

        live_db = f.hub / "pack.db"
        with patch.object(manager.subprocess, "run", side_effect=fail_verification):
            with self.assertRaises(subprocess.CalledProcessError):
                manager.restore_database_snapshot(backup_root)

        self.assertEqual(live_db.read_text(encoding="utf-8"), "original-db")
        self.assertFalse(list(state.glob("pack.db.failed-*")))
        self.assertFalse(list(f.hub.glob(".pack.db.rollback-*")))

    def test_rollback_refuses_to_swap_files_while_service_port_remains_occupied(self):
        f = self.fixture
        ctl, calls, loaded = f.fake_ctl(
            fail_watch_once=True,
            mutate_db_on_hub_bootstrap=True,
        )
        run, _ = f.fake_subprocess_run()
        availability = iter([True, True, False, True])
        with (
            patch.object(manager, "ctl", side_effect=ctl),
            patch.object(manager.subprocess, "run", side_effect=run),
            patch.object(manager, "available", side_effect=lambda port: next(availability)),
            patch.object(manager, "wait_for_hub_ready", return_value=None),
            patch.object(manager, "wait_for_port", return_value=None),
        ):
            with self.assertRaisesRegex(RuntimeError, "rollback needs attention"):
                manager.upgrade()

        self.assertEqual((f.hub / "hub.py").read_text(encoding="utf-8"), "new-hub")
        self.assertTrue((f.app_path / "new-app.txt").is_file())
        self.assertEqual((f.hub / "pack.db").read_text(encoding="utf-8"), "mutated-by-new-hub")
        self.assertEqual(loaded, set())
        self.assertEqual(sum(call[0] == "bootstrap" for call in calls), 2)
        self.assertTrue(list(f.base.glob(".pack-upgrade-*/previous/hub/hub.py")))
        self.assertTrue(list((f.hub / "backups").glob("upgrade-*")))

    def test_rollback_refuses_to_swap_files_while_new_hub_is_still_loaded(self):
        f = self.fixture
        ctl, calls, loaded = f.fake_ctl(
            fail_watch_once=True,
            mutate_db_on_hub_bootstrap=True,
            keep_hub_loaded_on_rollback=True,
        )
        run, _ = f.fake_subprocess_run()
        with (
            patch.object(manager, "ctl", side_effect=ctl),
            patch.object(manager.subprocess, "run", side_effect=run),
            patch.object(manager, "available", return_value=True),
            patch.object(manager, "wait_for_hub_ready", return_value=None),
            patch.object(manager, "wait_for_port", return_value=None),
        ):
            with self.assertRaisesRegex(RuntimeError, "rollback needs attention"):
                manager.upgrade()

        self.assertEqual((f.hub / "hub.py").read_text(encoding="utf-8"), "new-hub")
        self.assertTrue((f.app_path / "new-app.txt").is_file())
        self.assertEqual((f.hub / "pack.db").read_text(encoding="utf-8"), "mutated-by-new-hub")
        self.assertIn(f.labels["hub"], loaded)
        self.assertEqual(sum(call[0] == "bootstrap" for call in calls), 2)
        self.assertTrue(list(f.base.glob(".pack-upgrade-*/previous/hub/hub.py")))
        self.assertTrue(list((f.hub / "backups").glob("upgrade-*")))

    def test_reporting_failure_after_commit_does_not_roll_back_healthy_upgrade(self):
        f = self.fixture
        ctl, calls, loaded = f.fake_ctl()
        run, _ = f.fake_subprocess_run()
        with (
            patch.object(manager, "ctl", side_effect=ctl),
            patch.object(manager.subprocess, "run", side_effect=run),
            patch.object(manager, "available", return_value=True),
            patch.object(manager, "wait_for_hub_ready", return_value=None),
            patch.object(manager, "wait_for_port", return_value=None),
            patch("builtins.print", side_effect=BrokenPipeError("closed output pipe")),
        ):
            with self.assertRaisesRegex(RuntimeError, "new services are healthy"):
                manager.upgrade()

        self.assertEqual((f.hub / "hub.py").read_text(encoding="utf-8"), "new-hub")
        self.assertTrue((f.app_path / "new-app.txt").is_file())
        self.assertFalse((f.app_path / "old-app.txt").exists())
        self.assertEqual((f.hub / "pack.db").read_text(encoding="utf-8"), "original-db")
        self.assertEqual(loaded, set(f.labels.values()))
        self.assertTrue(list((f.hub / "backups").glob("upgrade-*")))
        self.assertEqual(sum(call[0] == "bootout" for call in calls), len(f.labels))

    def test_failed_restart_restores_previous_program_and_app(self):
        f = self.fixture
        ctl, calls, loaded = f.fake_ctl(fail_watch_once=True, mutate_db_on_hub_bootstrap=True)
        run, subprocess_calls = f.fake_subprocess_run()
        with (
            patch.object(manager, "ctl", side_effect=ctl),
            patch.object(manager.subprocess, "run", side_effect=run),
            patch.object(manager, "available", return_value=True),
            patch.object(manager, "wait_for_hub_ready", return_value=None),
            patch.object(manager, "wait_for_port", return_value=None),
        ):
            with self.assertRaisesRegex(RuntimeError, "previous program files and database snapshot were restored"):
                manager.upgrade()

        self.assertEqual((f.hub / "hub.py").read_text(encoding="utf-8"), "old-hub")
        self.assertEqual((f.hub / "core" / "context.py").read_text(encoding="utf-8"), "old-context")
        self.assertEqual((f.engine / "app" / "headless.cjs").read_text(encoding="utf-8"), "old-engine")
        self.assertTrue((f.app_path / "old-app.txt").is_file())
        self.assertFalse((f.app_path / "new-app.txt").exists())
        self.assertEqual((f.hub / "pack.db").read_text(encoding="utf-8"), "original-db")
        self.assertFalse((f.hub / "pack.db-wal").exists())
        self.assertFalse((f.hub / "pack.db-shm").exists())
        self.assertEqual((f.engine / "data" / "local" / "session.json").read_text(encoding="utf-8"), "original-session")
        rollback_states = list((f.hub / "backups").glob("upgrade-*/state"))
        self.assertTrue(any(list(state.glob("pack.db.failed-*")) for state in rollback_states))
        self.assertTrue(any(list(state.glob("pack.db-wal.failed-*")) for state in rollback_states))
        self.assertTrue(any(list(state.glob("pack.db-shm.failed-*")) for state in rollback_states))
        self.assertEqual(loaded, set(f.labels.values()))
        self.assertTrue(list((f.hub / "backups").glob("upgrade-*")))
        self.assertEqual(
            sum(Path(call[1]).name == "verify-backup.sh" for call in subprocess_calls if call[0] == "/bin/bash"),
            2,
        )
        self.assertTrue(any(call[0] == "bootout" for call in calls))


if __name__ == "__main__":
    unittest.main(verbosity=2)
