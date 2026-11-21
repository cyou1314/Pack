#!/usr/bin/env python3
"""Verify the Hub's copied runtime payload and all installer copy manifests."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from export_contracts import _copy_hub

ROOT = Path(__file__).resolve().parents[1]


class HubPackageLayoutTests(unittest.TestCase):
    def test_disposable_runtime_copy_registers_room_and_member_tools(self):
        with tempfile.TemporaryDirectory(prefix="pack-hub-payload-") as directory:
            root = Path(directory)
            _copy_hub(root)
            smoke = (
                "import asyncio,inspect,hub; tools=hub.build_server().list_tools(); "
                "tools=asyncio.run(tools) if inspect.isawaitable(tools) else tools; "
                "names={tool.name for tool in tools}; "
                "assert {'room_list','member_list'}.issubset(names), names"
            )
            result = subprocess.run(
                [sys.executable, "-c", smoke],
                cwd=root,
                env={**os.environ, "PYTHONPATH": str(root), "PYTHONNOUSERSITE": "1"},
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((root / "core" / "context.py").is_file())
            self.assertTrue((root / "services" / "rooms.py").is_file())

    def test_local_and_dmg_installers_copy_both_hub_packages(self):
        local = (ROOT / "local" / "install-local.sh").read_text(encoding="utf-8")
        dmg = (ROOT / "installer" / "build-dmg.sh").read_text(encoding="utf-8")
        self.assertIn('cp -R "$ROOT/hub/core" "$HUB/"', local)
        self.assertIn('cp -R "$ROOT/hub/services" "$HUB/"', local)
        self.assertIn('cp -R "$ROOT/hub/core" "$STAGE/hub/"', dmg)
        self.assertIn('cp -R "$ROOT/hub/services" "$STAGE/hub/"', dmg)
        self.assertIn('sys.path.insert(0,sys.argv[1])', dmg)

    def test_manager_installs_and_uninstalls_hub_packages(self):
        source = (ROOT / "installer" / "manager.py").read_text(encoding="utf-8")
        self.assertIn("BUNDLE/'hub'/'core'", source)
        self.assertIn("BUNDLE/'hub'/'services'", source)
        self.assertIn("('python','hub.py','core','services'", source)

    def test_local_uninstaller_removes_only_code_package_directories(self):
        source = (ROOT / "local" / "uninstall-local.sh").read_text(encoding="utf-8")
        self.assertIn("for name in core services", source)
        self.assertIn("including pack.db, attachments and backups", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
