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
    def test_disposable_runtime_copy_registers_room_member_and_task_tools(self):
        with tempfile.TemporaryDirectory(prefix="pack-hub-payload-") as directory:
            root = Path(directory)
            _copy_hub(root)
            smoke = (
                "import asyncio,inspect,hub; tools=hub.build_server().list_tools(); "
                "tools=asyncio.run(tools) if inspect.isawaitable(tools) else tools; "
                "names={tool.name for tool in tools}; "
                "assert {'room_list','member_list','task_audit','task_transfers_list','task_list','task_create','task_set_urgent','task_schedule','task_claim','task_update','task_complete','task_block','task_review','task_cancel','task_transfer','task_accept_transfer','task_release','task_clear_backoff','run_start','run_finish','message_edit','message_delete','room_post','inbox_send','inbox_pull','inbox_mark_read','attachment_get','attachment_list','attachment_put','rule_list','rule_upsert','rule_delete','egress_rule_list','egress_rule_upsert','egress_rule_delete','review_file','memory_read','memory_versions','memory_write','memory_rollback','skill_publish','skill_list','skill_read','skill_assign','skill_unassign','skill_rollback'}.issubset(names), names"
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
            self.assertTrue((root / "services" / "members.py").is_file())
            self.assertTrue((root / "services" / "tasks.py").is_file())
            self.assertTrue((root / "services" / "messages.py").is_file())
            self.assertTrue((root / "services" / "attachments.py").is_file())
            self.assertTrue((root / "services" / "attachment_audit.py").is_file())
            self.assertTrue((root / "services" / "rules.py").is_file())
            self.assertTrue((root / "services" / "reviews.py").is_file())
            self.assertTrue((root / "services" / "memory.py").is_file())
            self.assertTrue((root / "services" / "skills.py").is_file())

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
