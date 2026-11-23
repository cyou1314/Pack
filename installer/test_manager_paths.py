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


def test_failed_install_rollback():
    sys.path.insert(0, str(HERE))
    import manager

    with tempfile.TemporaryDirectory(prefix="pack-installer-rollback-test-") as tmp:
        root = pathlib.Path(tmp)
        hub = root / "local-hub"
        engine = root / "local-engine"
        config = root / "config.json"
        socket_dir = root / "handoff-socket"
        agents = root / "LaunchAgents"
        agents.mkdir()
        for path in (hub, engine, socket_dir):
            path.mkdir()
            (path / "partial-install-marker").write_text("owned")
        config.write_text("partial")
        plist = agents / "com.pack.installer-test-hub.plist"
        plist.write_text("partial")
        original_ctl = manager.ctl
        manager.ctl = lambda *args, **kwargs: None
        try:
            manager._rollback_install(
                [plist], (config, socket_dir, engine, hub), agents, True
            )
        finally:
            manager.ctl = original_ctl
        assert not any(path.exists() for path in (hub, engine, config, socket_dir, plist))
        assert agents.is_dir(), "pre-existing LaunchAgents directory must be preserved"

        fresh_agents = root / "FreshLaunchAgents"
        fresh_agents.mkdir()
        partial_hub = root / "fresh-local-hub"
        partial_hub.mkdir()
        (partial_hub / "partial-install-marker").write_text("owned")
        manager.ctl = lambda *args, **kwargs: None
        try:
            manager._rollback_install([], (partial_hub,), fresh_agents, False)
        finally:
            manager.ctl = original_ctl
        assert not partial_hub.exists()
        assert not fresh_agents.exists(), "new empty LaunchAgents directory must be removed"



def test_preexisting_dangling_destination_symlink():
    sys.path.insert(0, str(HERE))
    import manager

    with tempfile.TemporaryDirectory(prefix="pack-installer-dangling-link-") as tmp:
        root = pathlib.Path(tmp)
        originals = {
            name: getattr(manager, name)
            for name in ("hub", "engine", "config", "socket_dir", "agents", "available", "ctl")
        }
        manager.hub = root / "local-hub"
        manager.engine = root / "local-engine"
        manager.config = root / "config.json"
        manager.socket_dir = root / "handoff-socket"
        manager.agents = root / "LaunchAgents"
        manager.available = lambda _port: True

        class NotLoaded:
            returncode = 1

        manager.ctl = lambda *args, **kwargs: NotLoaded()
        manager.config.symlink_to(root / "missing-config-target")
        try:
            try:
                manager.install()
            except SystemExit as exc:
                assert "refusing to overwrite" in str(exc)
            else:
                raise AssertionError("install must reject a pre-existing dangling config symlink")
        finally:
            for name, value in originals.items():
                setattr(manager, name, value)

        assert manager.config.is_symlink() if manager.config == root / "config.json" else (root / "config.json").is_symlink()
        assert not (root / "local-hub").exists()
        assert not (root / "local-engine").exists()


def main():
    test_failed_install_rollback()
    test_preexisting_dangling_destination_symlink()
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

    print("PASS installer test-mode path isolation")


if __name__ == "__main__":
    main()
