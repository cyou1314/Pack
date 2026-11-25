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
        url_policy = subprocess.run(
            [sys.executable, "-c",
             "import manager; "
             "assert manager.is_allowed_hub_url('http://127.0.0.1:18795/mcp'); "
             "assert manager.is_allowed_hub_url('http://localhost:18795/mcp'); "
             "assert manager.is_allowed_hub_url('https://sub.run.pinggy-free.link/mcp'); "
             "assert not manager.is_allowed_hub_url('https://attacker.example/mcp'); "
             "assert not manager.is_allowed_hub_url('https://sub.pinggy-free.link/mcp?'); "
             "assert not manager.is_allowed_hub_url('http://@127.0.0.1:18795/mcp')"],
            cwd=HERE, env=env, text=True, capture_output=True,
        )
        assert url_policy.returncode == 0, url_policy.stderr
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

        # Never follow a symlink in place of the LaunchAgents directory.
        external_agents = home / "external-launchagents"
        external_agents.mkdir()
        linked_agents = home / "linked-launchagents"
        linked_agents.symlink_to(external_agents, target_is_directory=True)
        link_env = env.copy()
        link_env["PACK_TEST_AGENT_DIR"] = str(linked_agents)
        linked = subprocess.run(
            [sys.executable, "-c",
             "import os, pathlib, manager; "
             "manager.agents=pathlib.Path(os.environ['PACK_TEST_AGENT_DIR']); "
             "manager.ensure_agent_directory_safe()"],
            cwd=HERE, env=link_env, text=True, capture_output=True,
        )
        assert linked.returncode != 0, linked
        assert list(external_agents.iterdir()) == []

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


        # Exercise the whole install transaction with a fake payload and
        # injected failure after Hub startup. No launchctl, ports or Mac app
        # are touched; uninstall must preserve every generated data item.
        bundle = home / "fake-bundle"
        (bundle / "python" / "bin").mkdir(parents=True)
        (bundle / "python" / "bin" / "python3").write_text("#!/bin/sh\n")
        (bundle / "hub").mkdir()
        for name in ("hub.py", "room_watch.py", "backup-db.sh", "verify-backup.sh"):
            (bundle / "hub" / name).write_text("# fixture\n")
        (bundle / "engine" / "app").mkdir(parents=True)
        (bundle / "engine" / "app" / "headless.cjs").write_text("// fixture\n")
        (bundle / "engine" / "Electron.app" / "Contents" / "MacOS").mkdir(parents=True)
        (bundle / "engine" / "Electron.app" / "Contents" / "MacOS" / "Electron").write_text("fixture\n")
        transaction_env = env.copy()
        transaction_env["PACK_TEST_BUNDLE"] = str(bundle)
        transaction_script = r"""
import asyncio, json, os, pathlib, shutil, types, manager
manager.BUNDLE = pathlib.Path(os.environ["PACK_TEST_BUNDLE"])
manager.available = lambda port: True
loaded = set()
def fake_ctl(*args, check=True):
    command = args[0]
    label = args[-1].split("/")[-1]
    if command == "bootstrap":
        loaded.add(pathlib.Path(args[-1]).stem)
        return types.SimpleNamespace(returncode=0)
    if command == "print":
        return types.SimpleNamespace(returncode=0 if label in loaded else 1)
    if command == "bootout":
        loaded.discard(label)
        return types.SimpleNamespace(returncode=0)
    raise AssertionError(args)
manager.ctl = fake_ctl
class DummyConnection:
    def __enter__(self): return self
    def __exit__(self, *args): return False
manager.socket.create_connection = lambda *args, **kwargs: DummyConnection()
def fake_run(args, check=True, **kwargs):
    assert args[0] == "/usr/bin/ditto", args
    shutil.copytree(args[1], args[2])
    return types.SimpleNamespace(returncode=0)
manager.subprocess.run = fake_run
async def fake_setup_owner():
    manager.write_private(manager.hub / ".dispatcher_token", "dispatch-token")
    manager.write_private(manager.hub / ".owner_token", "owner-token")
    manager.write_private(manager.hub / "pack.db", "database-fixture")
    manager.write_private(manager.config, '{"hubUrl":"http://127.0.0.1:18795/mcp","token":"dispatch-token","ownerToken":"owner-token"}')
manager.setup_owner = fake_setup_owner
original_write_agent = manager.write_agent
def fail_after_watch(kind, *args, **kwargs):
    original_write_agent(kind, *args, **kwargs)
    if kind == "watch":
        raise RuntimeError("injected transaction failure")
manager.write_agent = fail_after_watch
try:
    manager.install()
except RuntimeError as exc:
    assert str(exc) == "injected transaction failure"
else:
    raise AssertionError("injected failure was not raised")
state = manager.load_install_state()
assert state["phase"] == "failed", state
assert manager.is_managed_root(manager.hub, "hub")
assert manager.is_managed_root(manager.engine, "engine")
assert manager.is_managed_root(manager.socket_dir, "socket")
assert not loaded, loaded
assert not (manager.agents / (manager.labels["hub"] + ".plist")).exists()
assert not (manager.agents / (manager.labels["watch"] + ".plist")).exists()
assert (manager.hub / ".owner_token").read_text() == "owner-token"
assert manager.config.is_file()
# Simulate SIGKILL: the durable journal remains in the installing phase.
state["phase"] = "installing"
manager.save_install_state(state)
try:
    manager.install()
except SystemExit as exc:
    assert "interrupted Pack installation" in str(exc)
else:
    raise AssertionError("interrupted transaction was not detected")
manager.uninstall()
state = manager.load_install_state()
assert state["phase"] == "uninstalled", state
assert not (manager.hub / "python").exists()
assert not (manager.hub / "hub.py").exists()
assert not (manager.engine / "app").exists()
assert not (manager.engine / "Electron.app").exists()
assert (manager.hub / "attachments").is_dir()
assert (manager.hub / ".dispatcher_token").read_text() == "dispatch-token"
assert (manager.hub / ".owner_token").read_text() == "owner-token"
assert (manager.hub / "pack.db").read_text() == "database-fixture"
assert manager.config.is_file()
assert (manager.engine / "data" / "local").is_dir()
assert not manager.socket_dir.exists()
assert not loaded, loaded

# Reinstall the program payload into the same Pack-owned data roots. Existing
# credentials, database, attachments and engine data must remain untouched.
manager.write_agent = original_write_agent
async def unexpected_owner_setup():
    raise AssertionError("reinstall must not register a second owner")
manager.setup_owner = unexpected_owner_setup
# Simulate Pack App having migrated dispatcher/owner credentials to Keychain:
# the config keeps hubUrl only, while private token files remain preserved.
config_data = json.loads(manager.config.read_text())
config_data.pop("token", None)
config_data.pop("ownerToken", None)
manager.config.write_text(json.dumps(config_data), encoding="utf-8")
saved_config = manager.config.read_text()

# Reinstall must reject symlinked database and attachment paths without
# following them or mutating their external targets.
db_path = manager.hub / "pack.db"
db_contents = db_path.read_text()
external_db = manager.base / "external-db-target"
external_db.write_text("external-data")
db_path.unlink()
db_path.symlink_to(external_db)
try:
    manager.validate_reinstall_data()
except SystemExit as exc:
    assert "database path is unsafe" in str(exc)
else:
    raise AssertionError("symlinked database was accepted for reinstall")
db_path.unlink()
db_path.write_text(db_contents)
assert external_db.read_text() == "external-data"

attachments_path = manager.hub / "attachments"
saved_attachments = manager.hub / "attachments-preserved"
external_attachments = manager.base / "external-attachments"
external_attachments.mkdir()
attachments_path.rename(saved_attachments)
attachments_path.symlink_to(external_attachments, target_is_directory=True)
try:
    manager.validate_reinstall_data()
except SystemExit as exc:
    assert "data path is unsafe" in str(exc)
else:
    raise AssertionError("symlinked attachments directory was accepted")
attachments_path.unlink()
saved_attachments.rename(attachments_path)
assert list(external_attachments.iterdir()) == []

manager.install()
state = manager.load_install_state()
assert state["phase"] == "completed", state
assert manager.config.read_text() == saved_config
assert (manager.hub / ".dispatcher_token").read_text() == "dispatch-token"
assert (manager.hub / ".owner_token").read_text() == "owner-token"
assert (manager.hub / "pack.db").read_text() == "database-fixture"
assert (manager.hub / "python").is_dir()
assert (manager.engine / "app").is_dir()
assert manager.socket_dir.is_dir()
assert len(loaded) == 4, loaded
manager.uninstall()
state = manager.load_install_state()
assert state["phase"] == "uninstalled", state
assert not (manager.hub / "python").exists()
assert not (manager.engine / "app").exists()
assert (manager.hub / "pack.db").read_text() == "database-fixture"
assert manager.config.read_text() == saved_config
assert not loaded, loaded
"""
        transaction = subprocess.run(
            [sys.executable, "-c", transaction_script],
            cwd=HERE, env=transaction_env, text=True, capture_output=True,
        )
        assert transaction.returncode == 0, transaction.stdout + transaction.stderr

    print("PASS installer path isolation, no-clobber, LaunchAgent ownership, and transaction recovery")


if __name__ == "__main__":
    main()
