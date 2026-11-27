#!/usr/bin/env python3
"""Build/review a program-only browser payload manifest; not release approval."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

DRIVER_FILES = (
    "package.json", "package-lock.json", "sessions.mjs", "playwright.mjs",
    "native-login.mjs", "native-login-cli.mjs", "native-diagnostics.mjs",
    "native-identity.mjs", "native-account-adapter.mjs", "chatgpt-page-driver.mjs",
    "chatgpt-collaboration-host.mjs", "native-collaboration-host.mjs", "native-hub-task-client.mjs", "native-task-pipe.mjs",
)
RUNTIME_FILES = ("account-execution-leases.mjs", "collaboration-runner.mjs")
AGENT_CORE_FILES = ("collaboration-execution-supervisor.mjs",)
BROWSERS = ("chromium-1255", "chromium_headless_shell-1255", "ffmpeg-1013")
PRIVATE_NAMES = {"cookies", "login data", "web data", "local state", "history",
                 "browser-data", "native-sessions", "native-identities",
                 "execution-leases", "agent-registry.json", "pack.db",
                 ".pack-session-lease", "preferences", "secure preferences"}


def exact(directory, expected):
    if not stat.S_ISDIR(directory.lstat().st_mode):
        raise ValueError("payload directory must not be a symlink")
    if {p.name for p in directory.iterdir()} != set(expected):
        raise ValueError("payload directory entries do not match allowlist")


def regular(file, executable=False):
    mode = file.lstat().st_mode
    if not stat.S_ISREG(mode) or executable and not mode & 0o111:
        raise ValueError("missing regular payload file or executable")
    return file


def digest(file):
    with regular(file).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def tree_manifest(root):
    """Relative names, type, executable bits, SHA-256, and safe symlinks.
    Timestamp, ownership, xattrs, and signature trust are separate checks.
    """
    entries = []

    def walk(directory):
        for p in sorted(directory.iterdir(), key=lambda item: item.name):
            rel = p.relative_to(root).as_posix()
            if p.name.lower() in PRIVATE_NAMES or p.name.lower().endswith((".sqlite", ".sqlite3", ".db", ".db-wal")):
                raise ValueError("unexpected private data in browser payload")
            mode = p.lstat().st_mode
            if stat.S_ISLNK(mode):
                target = os.readlink(p)
                if os.path.isabs(target) or not p.resolve(strict=True).is_relative_to(root):
                    raise ValueError("unsafe browser payload symlink")
                entries.append([rel, "link", target])
            elif stat.S_ISDIR(mode):
                entries.append([rel, "dir"])
                walk(p)
            elif stat.S_ISREG(mode):
                entries.append([rel, "file", mode & 0o111, digest(p)])
            else:
                raise ValueError("unsupported browser payload entry")
    walk(root)
    return entries


def snapshot(payload, revision, architecture):
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("full source revision required")
    if architecture not in ("arm64", "x86_64"):
        raise ValueError("unsupported target architecture")
    root = Path(payload)
    exact(root, ("node", "runtime", "browsers"))
    root = root.resolve()
    exact(root / "node", ("bin",))
    exact(root / "node/bin", ("node",))
    regular(root / "node/bin/node", executable=True)
    runtime = root / "runtime"
    exact(runtime, (*RUNTIME_FILES, "browser-driver", "agent-core"))
    exact(runtime / "agent-core", AGENT_CORE_FILES)
    for name in AGENT_CORE_FILES:
        regular(runtime / "agent-core" / name)
    for name in RUNTIME_FILES:
        regular(runtime / name)
    driver = runtime / "browser-driver"
    exact(driver, (*DRIVER_FILES, "node_modules"))
    for name in DRIVER_FILES:
        regular(driver / name)
    exact(driver / "node_modules", ("playwright-core",))
    core = driver / "node_modules/playwright-core"
    if not stat.S_ISDIR(core.lstat().st_mode):
        raise ValueError("invalid dependency directory")
    manifest = json.loads(regular(driver / "package.json").read_text())
    lock = json.loads(regular(driver / "package-lock.json").read_text())
    installed = json.loads(regular(core / "package.json").read_text())
    version = manifest.get("dependencies", {}).get("playwright-core")
    if version != "1.65.1" or installed.get("version") != version or installed.get("name") != "playwright-core":
        raise ValueError("unexpected Playwright version")
    if lock.get("packages", {}).get("node_modules/playwright-core", {}).get("version") != version:
        raise ValueError("unexpected Playwright lock version")
    regular(core / "LICENSE")
    exact(root / "browsers", BROWSERS)
    for name in BROWSERS:
        folder = root / "browsers" / name
        if not stat.S_ISDIR(folder.lstat().st_mode) or not any(folder.iterdir()):
            raise ValueError("missing browser build directory")
    entries = tree_manifest(root)
    serialized = json.dumps(entries, ensure_ascii=True, separators=(",", ":")).encode()
    return {"schema_version": 1, "source_revision": revision, "architecture": architecture,
            "playwright_version": version, "release_approved": False,
            "tree_sha256": hashlib.sha256(serialized).hexdigest(), "entries": entries}


def create(payload, lock, revision, architecture):
    data = snapshot(payload, revision, architecture)
    with Path(lock).open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")
    return data


def verify(payload, lock, revision, architecture):
    expected = json.loads(regular(Path(lock)).read_text(encoding="utf-8"))
    if expected != snapshot(payload, revision, architecture):
        raise ValueError("browser payload does not match candidate manifest")
    return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "verify"])
    parser.add_argument("--payload", required=True)
    parser.add_argument("--lock", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--architecture", required=True)
    args = parser.parse_args()
    try:
        operation = create if args.action == "create" else verify
        result = operation(args.payload, args.lock, args.revision, args.architecture)
        print(result["tree_sha256"])
    except (OSError, ValueError, RuntimeError, TypeError) as exc:
        print(f"Browser candidate payload: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
