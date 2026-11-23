#!/usr/bin/env python3
"""Static packaging-contract tests; the actual DMG build remains a macOS gate."""
import os
import plistlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DmgManifestTests(unittest.TestCase):
    def test_app_info_plist_has_version_and_platform_metadata(self):
        info = plistlib.loads((ROOT / "app" / "Info.plist").read_bytes())
        self.assertEqual(info.get("CFBundleIdentifier"), "com.cyou.pack")
        self.assertTrue(info.get("CFBundleShortVersionString"))
        self.assertRegex(info.get("CFBundleVersion", ""), r"^[0-9]+$")
        self.assertEqual(info.get("LSMinimumSystemVersion"), "14.0")

    def test_bundle_builder_uses_explicit_non_destructive_output(self):
        source = (ROOT / "app" / "make-bundle.sh").read_text(encoding="utf-8")
        self.assertIn('APP="${1:-$ROOT/Pack.app}"', source)
        self.assertIn('[[ ! -e "$APP" && ! -L "$APP" ]]', source)
        self.assertNotIn('rm -rf "$APP"', source)
        self.assertIn("codesign --verify --deep --strict", source)

    def test_dmg_builder_creates_bundle_from_source_and_records_manifest(self):
        source = (ROOT / "installer" / "build-dmg.sh").read_text(encoding="utf-8")
        self.assertIn('bash "$ROOT/app/make-bundle.sh" "$STAGE/Pack.app"', source)
        self.assertNotIn('ditto "$ROOT/app/Pack.app"', source)
        for key in (
            "BUILD-MANIFEST.txt",
            "PackVersion=",
            "PackBuild=",
            "MinimumMacOS=",
            "PythonVersion=",
            "FastMCPVersion=",
            "EngineVersion=",
            "SourceRevision=",
        ):
            self.assertIn(key, source)
        self.assertIn('hdiutil verify "$OUT"', source)
        self.assertIn('shasum -a 256 "$OUT"', source)

    def test_dmg_payload_guard_rejects_known_user_data_and_secrets(self):
        source = (ROOT / "installer" / "build-dmg.sh").read_text(encoding="utf-8")
        for pattern in (
            "pack.db*",
            "muse.sqlite*",
            ".dispatcher_token",
            ".owner_token",
            "tokens.json",
            "*.token",
            "Cookies",
            "vault.json",
            "Partitions",
        ):
            self.assertIn(pattern, source)

    def test_readme_documents_the_build_manifest(self):
        source = (ROOT / "installer" / "README.txt").read_text(encoding="utf-8")
        self.assertIn("BUILD-MANIFEST.txt", source)
        self.assertIn("SHA-256", source)

    def test_dmg_contains_a_dedicated_upgrade_command(self):
        source = (ROOT / "installer" / "build-dmg.sh").read_text(encoding="utf-8")
        command = (ROOT / "installer" / "upgrade.command").read_text(encoding="utf-8")
        readme = (ROOT / "installer" / "README.txt").read_text(encoding="utf-8")
        self.assertIn("upgrade.command", source)
        self.assertIn('manager.py" upgrade', command)
        self.assertIn("已有安装升级", readme)
        self.assertTrue(os.access(ROOT / "installer" / "upgrade.command", os.X_OK))
        self.assertTrue(os.access(ROOT / "installer" / "build-dmg.sh", os.X_OK))

    def test_mac_inventory_is_read_only_and_does_not_emit_secret_values(self):
        source = (ROOT / "installer" / "mac-inventory.sh").read_text(encoding="utf-8")
        self.assertIn("launchctl print", source)
        self.assertIn("sqlite3 -readonly", source)
        self.assertNotIn("-maxdepth", source)
        self.assertIn("EntryPointValidation=expected-basename", source)
        self.assertIn("EntryPointValidation=mismatch", source)
        self.assertNotIn("Print :ProgramArguments'", source)
        self.assertIn("Keychain=not queried", source)
        self.assertNotIn("security find-generic-password", source)
        self.assertNotIn('cat "$CONFIG"', source)
        self.assertNotIn('cat "$HUB/.dispatcher_token"', source)
        self.assertTrue(os.access(ROOT / "installer" / "mac-inventory.sh", os.X_OK))


if __name__ == "__main__":
    unittest.main(verbosity=2)
