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
        self.assertIn('APP_INPUT="${1:-$SCRIPT_DIR/Pack.app}"', source)
        self.assertIn('if [ -e "$APP" ] || [ -L "$APP" ]; then', source)
        self.assertIn('if ! mkdir "$APP"; then', source)
        self.assertIn("Output path appeared during build", source)
        self.assertIn('TMP_APP="$TMP_ROOT/Pack.app"', source)
        self.assertIn("codesign --verify --deep --strict", source)
        self.assertTrue(os.access(ROOT / "app" / "make-bundle.sh", os.X_OK))

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

    def test_dmg_build_requires_release_and_arm64_payloads(self):
        source = (ROOT / "installer" / "build-dmg.sh").read_text(encoding="utf-8")
        bundle = (ROOT / "app" / "make-bundle.sh").read_text(encoding="utf-8")
        self.assertIn('PACK_BUILD_CONFIGURATION=release bash "$ROOT/app/make-bundle.sh"', source)
        self.assertIn('CONFIGURATION="${PACK_BUILD_CONFIGURATION:-debug}"', bundle)
        self.assertIn('swift build -c "$CONFIGURATION"', bundle)
        self.assertIn('cp ".build/$CONFIGURATION/PackApp"', bundle)
        self.assertIn('[[ "$(uname -m)" == "arm64" ]]', source)
        self.assertIn('lipo -archs "$ENGINE_BIN"', source)
        self.assertIn('lipo -archs "$STAGE/Pack.app/Contents/MacOS/PackApp"', source)
        for key in ("HostArchitecture=", "AppBuildConfiguration=release", "AppArchitectures=",
                    "PythonArchitecture=", "EngineArchitectures="):
            self.assertIn(key, source)

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

    def test_isolated_upgrade_instructions_stage_the_old_app_bundle(self):
        source = (ROOT / "docs" / "v2" / "MAC-MINIMAL-CHANGE-DEPLOYMENT.md").read_text(encoding="utf-8")
        self.assertIn('mkdir -p "$PACK_INSTALL_TEST_ROOT/Applications"', source)
        self.assertIn('ditto "$DMG_OLD/Pack.app" "$PACK_INSTALL_TEST_ROOT/Applications/Pack.app"', source)

    def test_upgrade_rollback_does_not_overwrite_live_user_data(self):
        manager_source = (ROOT / "installer" / "manager.py").read_text(encoding="utf-8")
        readme = (ROOT / "installer" / "README.txt").read_text(encoding="utf-8")
        self.assertNotIn("restore_database_snapshot", manager_source)
        self.assertIn("Live DB/attachments were not overwritten", manager_source)
        self.assertIn("不会自动用预升级快照覆盖 live DB/attachments", readme)

    def test_mac_inventory_is_read_only_and_does_not_emit_secret_values(self):
        source = (ROOT / "installer" / "mac-inventory.sh").read_text(encoding="utf-8")
        self.assertIn("launchctl print", source)
        self.assertIn("com.muse.pack-runtime", source)
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
