#!/usr/bin/env python3
"""Verify installer test mode never points at the user's real LaunchAgents."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
INSPECT = (
    "import json, manager; "
    "print(json.dumps({"
    "'home': str(manager.HOME), "
    "'base': str(manager.base), "
    "'hub': str(manager.hub), "
    "'engine': str(manager.engine), "
    "'agents': str(manager.agents), "
    "'app_path': str(manager.app_path), "
    "'prefix': manager.prefix, "
    "'hub_port': manager.hp, "
    "'engine_port': manager.ep"
    "}))"
)


def inspect(env):
    result = subprocess.run(
        [sys.executable, "-c", INSPECT],
        cwd=HERE,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def main():
    with tempfile.TemporaryDirectory(prefix="pack-installer-path-test-") as tmp:
        home = pathlib.Path(tmp)
        test_root = home / "commander-work" / "isolated-installer-check"
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["PYTHONPATH"] = str(HERE)
        env["PACK_INSTALL_TEST_ROOT"] = str(test_root)

        test = inspect(env)
        assert pathlib.Path(test["agents"]) == test_root / "LaunchAgents", test
        assert pathlib.Path(test["agents"]) != home / "Library" / "LaunchAgents", test
        assert pathlib.Path(test["hub"]) == test_root / "local-hub", test
        assert pathlib.Path(test["engine"]) == test_root / "local-engine", test
        assert pathlib.Path(test["app_path"]) == test_root / "Applications" / "Pack.app", test
        assert test["prefix"] == "com.pack.installer-test", test
        assert test["hub_port"] == 18795 and test["engine_port"] == 47822, test
        assert not (home / "Library" / "LaunchAgents").exists()

        production_env = env.copy()
        production_env.pop("PACK_INSTALL_TEST_ROOT")
        production = inspect(production_env)
        assert pathlib.Path(production["agents"]) == home / "Library" / "LaunchAgents", production
        assert pathlib.Path(production["hub"]) == home / ".pack" / "local-hub", production
        assert production["prefix"] == "com.pack.local", production

        # Existing LaunchAgent files must be rejected without truncation.
        agent_dir = test_root / "conflict-check"
        check_env = env.copy()
        check_env["PACK_TEST_AGENT_DIR"] = str(agent_dir)
        check = subprocess.run(
            [sys.executable, "-c",
             "import os, pathlib, manager; "
             "manager.agents = pathlib.Path(os.environ['PACK_TEST_AGENT_DIR']); "
             "manager.agents.mkdir(parents=True, exist_ok=True); "
             "manager.ensure_agent_paths_available()"],
            cwd=HERE, env=check_env, text=True, capture_output=True,
        )
        assert check.returncode == 0, check.stderr

        existing = agent_dir / (test["prefix"] + "-hub.plist")
        existing.write_text("preserve-existing-plist\\n", encoding="utf-8")
        check = subprocess.run(
            [sys.executable, "-c",
             "import os, pathlib, manager; "
             "manager.agents = pathlib.Path(os.environ['PACK_TEST_AGENT_DIR']); "
             "manager.ensure_agent_paths_available()"],
            cwd=HERE, env=check_env, text=True, capture_output=True,
        )
        assert check.returncode != 0, check
        assert "refusing to overwrite" in check.stderr, check.stderr
        assert existing.read_text(encoding="utf-8") == "preserve-existing-plist\\n"

        # Uninstall/rollback ownership checks: marked and exact legacy Pack
        # agents are accepted; a foreign command under a colliding label is not.
        ownership = subprocess.run(
            [sys.executable, "-c",
             "import json, pathlib, plistlib, manager; "
             "p=pathlib.Path(manager.agents); p.mkdir(parents=True, exist_ok=True); "
             "label=manager.labels['hub']; "
             "good={'Label':label,'PackInstallerMarker':manager.INSTALLER_MARKER,"
             "'WorkingDirectory':str(manager.hub),'ProgramArguments':[str(manager.hub/'python/bin/python3'),"
             "str(manager.hub/'hub.py'),'serve']}; "
             "marked=p/'marked.plist'; marked.write_bytes(plistlib.dumps(good)); "
             "legacy={'Label':label,'WorkingDirectory':str(manager.hub),"
             "'ProgramArguments':[str(manager.hub/'python/bin/python3'),"
             "str(manager.hub/'hub.py'),'serve']}; "
             "old=p/'legacy.plist'; old.write_bytes(plistlib.dumps(legacy)); "
             "foreign={'Label':label,'WorkingDirectory':str(manager.hub),"
             "'ProgramArguments':['/tmp/unrelated-agent']}; "
             "bad=p/'foreign.plist'; bad.write_bytes(plistlib.dumps(foreign)); "
             "print(json.dumps([manager.is_managed_agent(marked,label),"
             "manager.is_managed_agent(old,label),manager.is_managed_agent(bad,label)]))"],
            cwd=HERE, env=env, text=True, capture_output=True, check=True,
        )
        assert json.loads(ownership.stdout) == [True, True, False], ownership.stdout

    print("PASS installer path isolation, no-clobber, and LaunchAgent ownership")


if __name__ == "__main__":
    main()
