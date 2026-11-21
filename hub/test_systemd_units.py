#!/usr/bin/env python3
"""Static safety contract for Pack's systemd service units and installer."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SYSTEMD = ROOT / "hub" / "systemd"
INSTALLER = ROOT / "hub" / "install-services.sh"

REQUIRED = {
    "User": "hatch",
    "Group": "hatch",
    "UMask": "0077",
    "NoNewPrivileges": "true",
    "PrivateTmp": "true",
    "ProtectSystem": "full",
}
EXPECTED_SERVICES = {
    "pack-hub.service",
    "pack-room-watch.service",
    "pack-tunnel.service",
    "pack-db-backup.service",
    "pack-url-publisher.service",
}


def service_directives(text):
    section = None
    result = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section == "Service" and "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    return result


class SystemdUnitTests(unittest.TestCase):
    def test_all_expected_services_exist(self):
        actual = {path.name for path in SYSTEMD.glob("*.service")}
        self.assertTrue(EXPECTED_SERVICES.issubset(actual), EXPECTED_SERVICES - actual)

    def test_every_service_uses_least_privilege_baseline(self):
        services = sorted(SYSTEMD.glob("*.service"))
        self.assertTrue(services, "no systemd service units found")
        for path in services:
            with self.subTest(service=path.name):
                directives = service_directives(path.read_text(encoding="utf-8"))
                for key, expected in REQUIRED.items():
                    self.assertEqual(directives.get(key), expected, f"{path.name}: {key}")
        publisher = service_directives((SYSTEMD / "pack-url-publisher.service").read_text(encoding="utf-8"))
        self.assertIn("systemd-journal", publisher.get("SupplementaryGroups", "").split())

    def test_installer_uses_repo_relative_units_and_restarts_services(self):
        script = INSTALLER.read_text(encoding="utf-8")
        self.assertIn('SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"', script)
        self.assertIn('install -o root -g root -m 0644', script)
        self.assertIn('chown -R hatch:hatch "$SCRIPT_DIR"', script)
        self.assertIn('chown -R hatch:hatch "$ADDR_MIRROR"', script)
        self.assertIn('[[ -f "$SSH_DIR/pack_addr_ed25519" ]]', script)
        self.assertIn('chmod 0600 "$private_key"', script)
        self.assertLess(script.index('[[ -f "$UNIT_DIR/$unit" ]]'), script.index('chown -R hatch:hatch "$SCRIPT_DIR"'))
        self.assertNotIn("/home/hatch/workspace/pack/hub/systemd", script)
        self.assertEqual(script.count("systemctl daemon-reload"), 1)
        for unit in ("pack-hub.service", "pack-room-watch.service", "pack-tunnel.service"):
            self.assertIn(unit, script)
        self.assertIn("systemctl restart pack-hub.service pack-room-watch.service pack-tunnel.service", script)
        self.assertIn("systemctl restart pack-url-publisher.timer pack-db-backup.timer", script)


if __name__ == "__main__":
    unittest.main()
