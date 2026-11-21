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

    def test_owned_agent_plist_requires_expected_label_and_program_root(self):
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
            plist = root / "agent.plist"
            document = {
                "Label": labels["hub"],
                "ProgramArguments": [str(hub / ".venv/bin/python"), str(hub / "hub.py")],
            }
            plist.write_bytes(plistlib.dumps(document))

            with patch.object(manager, "hub", hub), patch.object(manager, "engine", engine), patch.object(manager, "labels", labels):
                self.assertEqual(manager.read_owned_agent_plist("hub", plist)["Label"], labels["hub"])

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

    def test_pack_runtime_launch_agent_uses_private_log_umask(self):
        document = plistlib.loads((ROOT / "runtime" / "com.muse.pack-runtime.plist").read_bytes())
        self.assertEqual(document.get("Label"), "com.muse.pack-runtime")
        self.assertEqual(document.get("Umask"), 0o077)

    def test_local_install_refuses_existing_paths_and_loaded_labels_before_writing(self):
        source = (ROOT / "local" / "install-local.sh").read_text(encoding="utf-8")
        self.assertIn('[[ ! -L "$HUB" && ! -e "$HUB" ]]', source)
        self.assertIn('launchctl print "gui/$UID/$label"', source)
        self.assertLess(source.index("for label in"), source.index('mkdir -p "$PACK_HOME" "$HUB" "$AGENTS"'))

    def test_local_uninstall_validates_agent_identity_before_bootout(self):
        source = (ROOT / "local" / "uninstall-local.sh").read_text(encoding="utf-8")
        self.assertIn('[[ ! -L "$path" ]] || fail', source)
        self.assertIn('plutil -extract Label raw -o - "$plist"', source)
        self.assertIn("ProgramArguments:1", source)
        self.assertLess(source.index("plutil -extract Label"), source.index("launchctl bootout"))
        self.assertIn('including pack.db, attachments and backups', source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
