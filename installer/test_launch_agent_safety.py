#!/usr/bin/env python3
"""Safety checks for Pack's macOS LaunchAgent installers and uninstallers."""
import importlib.util
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pack_installer_manager", ROOT / "installer" / "manager.py")
manager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manager)


class InstallerPathSafetyTests(unittest.TestCase):
    def test_real_directory_rejects_symlinks_and_regular_files(self):
        with tempfile.TemporaryDirectory(prefix="pack-path-safety-") as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir()
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(SystemExit, "symlinked"):
                manager.require_real_directory(link, "test directory")

            regular_file = root / "not-a-directory"
            regular_file.write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "not a directory"):
                manager.require_real_directory(regular_file, "test directory")

    def test_absent_path_guard_detects_dangling_symlink(self):
        with tempfile.TemporaryDirectory(prefix="pack-path-safety-") as directory:
            root = Path(directory)
            link = root / "dangling"
            link.symlink_to(root / "missing-target")
            with self.assertRaisesRegex(SystemExit, "refusing to overwrite"):
                manager.require_absent(link, "test path")

    def test_owned_agent_plist_requires_exact_entry_points(self):
        with tempfile.TemporaryDirectory(prefix="pack-agent-plist-") as directory:
            root = Path(directory)
            hub = root / "local-hub"
            engine = root / "local-engine"
            hub.mkdir()
            engine.mkdir()
            labels = {
                "hub": "com.pack.local-hub",
                "watch": "com.pack.local-watch",
                "backup": "com.pack.local-backup",
                "engine": "com.pack.local-engine",
            }
            expected_arguments = {
                "hub": [str(hub / "python/bin/python3"), str(hub / "hub.py"), "serve"],
                "watch": [str(hub / "python/bin/python3"), str(hub / "room_watch.py")],
                "backup": ["/bin/bash", str(hub / "backup-db.sh")],
                "engine": [
                    str(engine / "Electron.app/Contents/MacOS/Electron"),
                    str(engine / "app"),
                ],
            }
            with patch.object(manager, "hub", hub), patch.object(manager, "engine", engine), patch.object(manager, "labels", labels):
                for kind, arguments in expected_arguments.items():
                    with self.subTest(kind=kind):
                        plist = root / (kind + ".plist")
                        plist.write_bytes(plistlib.dumps({
                            "Label": labels[kind],
                            "ProgramArguments": arguments,
                        }))
                        self.assertEqual(
                            manager.read_owned_agent_plist(kind, plist)["ProgramArguments"],
                            arguments,
                        )

                plist = root / "hub.plist"
                document = {
                    "Label": labels["hub"],
                    "ProgramArguments": expected_arguments["hub"].copy(),
                }
                document["ProgramArguments"][1] = str(root / "unrelated" / "hub.py")
                plist.write_bytes(plistlib.dumps(document))
                with self.assertRaisesRegex(SystemExit, "outside the Pack service directory"):
                    manager.read_owned_agent_plist("hub", plist)

                plist.unlink()
                link = root / "agent-link.plist"
                link.symlink_to(plist)
                with self.assertRaisesRegex(SystemExit, "symlinked"):
                    manager.read_owned_agent_plist("hub", link)


class LaunchAgentTemplateTests(unittest.TestCase):
    def test_local_service_templates_use_private_umask_and_expected_labels(self):
        expected = {
            "com.pack.local-hub.plist": "com.pack.local-hub",
            "com.pack.local-watch.plist": "com.pack.local-watch",
            "com.pack.local-backup.plist": "com.pack.local-backup",
        }
        for filename, label in expected.items():
            with self.subTest(filename=filename):
                document = plistlib.loads((ROOT / "local" / filename).read_bytes())
                self.assertEqual(document.get("Label"), label)
                self.assertEqual(document.get("Umask"), 0o077)
                self.assertTrue(document.get("ProgramArguments"))
                self.assertTrue(str(document.get("StandardOutPath", "")).startswith("__HUB__/"))

    def test_local_scripts_use_non_reserved_user_id_variable(self):
        for filename in ("install-local.sh", "uninstall-local.sh"):
            with self.subTest(filename=filename):
                source = (ROOT / "local" / filename).read_text(encoding="utf-8")
                self.assertIn('USER_ID="$(id -u)"', source)
                self.assertNotIn('UID="$(id -u)"', source)

    def test_manager_keeps_pack_data_root_private(self):
        source = (ROOT / "installer" / "manager.py").read_text(encoding="utf-8")
        self.assertIn("base.chmod(0o700)", source)

    def test_pack_runtime_launch_agent_uses_private_log_umask(self):
        document = plistlib.loads((ROOT / "runtime" / "com.muse.pack-runtime.plist").read_bytes())
        self.assertEqual(document.get("Label"), "com.muse.pack-runtime")
        self.assertEqual(document.get("Umask"), 0o077)

    def test_local_install_refuses_existing_paths_and_loaded_labels_before_writing(self):
        source = (ROOT / "local" / "install-local.sh").read_text(encoding="utf-8")
        self.assertIn('[[ ! -L "$HUB" && ! -e "$HUB" ]]', source)
        self.assertIn('launchctl print "gui/$USER_ID/$label"', source)
        self.assertLess(source.index("for label in"), source.index('mkdir -p "$PACK_HOME" "$HUB" "$AGENTS"'))

    def test_local_uninstall_validates_agent_identity_before_bootout(self):
        source = (ROOT / "local" / "uninstall-local.sh").read_text(encoding="utf-8")
        self.assertIn('[[ ! -L "$path" ]] || fail', source)
        self.assertIn('plutil -extract Label raw -o - "$plist"', source)
        self.assertIn("ProgramArguments:0", source)
        self.assertIn("ProgramArguments:1", source)
        self.assertLess(source.index("plutil -extract Label"), source.index("launchctl bootout"))
        self.assertIn('including pack.db, attachments and backups', source)



class InstallRollbackTests(unittest.TestCase):
    def _run_injected_copy_failure(self, agents_preexisting):
        with tempfile.TemporaryDirectory(prefix="pack-install-rollback-") as directory:
            root = Path(directory)
            home = root / "home"
            (home / "Library").mkdir(parents=True)
            base = root / "pack"
            base.mkdir()
            base_marker = base / "keep-me"
            base_marker.write_text("user data", encoding="utf-8")
            agents = home / "Library" / "LaunchAgents"
            if agents_preexisting:
                agents.mkdir()
                agent_marker = agents / "keep-me"
                agent_marker.write_text("existing agent data", encoding="utf-8")
            else:
                agent_marker = None

            bundle = root / "bundle"
            for path in (
                bundle / "python",
                bundle / "hub" / "core",
                bundle / "hub" / "services",
                bundle / "engine" / "app",
                bundle / "engine" / "Electron.app",
            ):
                path.mkdir(parents=True, exist_ok=True)
            for name in ("hub.py", "room_watch.py", "backup-db.sh", "verify-backup.sh"):
                (bundle / "hub" / name).write_text("stub", encoding="utf-8")

            labels = {
                "hub": "com.pack.installer-test-hub",
                "watch": "com.pack.installer-test-watch",
                "backup": "com.pack.installer-test-backup",
                "engine": "com.pack.installer-test-engine",
            }
            with patch.multiple(
                manager,
                HOME=home,
                base=base,
                hub=base / "local-hub",
                engine=base / "local-engine",
                config=base / "config.json",
                socket_dir=base / "handoff-socket",
                agents=agents,
                BUNDLE=bundle,
                labels=labels,
                uid=501,
            ), patch.object(manager, "available", return_value=True), patch.object(
                manager, "ctl", return_value=type("CtlResult", (), {"returncode": 1})()
            ):
                real_copytree = shutil.copytree
                calls = 0

                def fail_second_copy(source, destination, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        raise OSError("injected staging copy failure")
                    return real_copytree(source, destination, **kwargs)

                with patch.object(manager.shutil, "copytree", side_effect=fail_second_copy):
                    with self.assertRaisesRegex(OSError, "injected staging copy failure"):
                        manager.install()

            self.assertFalse((base / "local-hub").exists())
            self.assertFalse((base / "local-engine").exists())
            self.assertFalse((base / "config.json").exists())
            self.assertFalse((base / "handoff-socket").exists())
            self.assertTrue(base_marker.is_file(), "pre-existing Pack data root must be preserved")
            if agents_preexisting:
                self.assertTrue(agent_marker.is_file(), "pre-existing LaunchAgents contents must be preserved")
            else:
                self.assertFalse(agents.exists(), "new LaunchAgents directory must be removed")

    def test_copy_failure_rolls_back_new_install_paths(self):
        self._run_injected_copy_failure(agents_preexisting=False)

    def test_copy_failure_preserves_preexisting_launch_agents(self):
        self._run_injected_copy_failure(agents_preexisting=True)



if __name__ == "__main__":
    unittest.main(verbosity=2)
