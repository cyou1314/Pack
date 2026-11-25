#!/usr/bin/env python3
"""Lock the exact external Engine payload; no network or source-tree changes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

APP_FILES = ("headless.cjs", "gateway-page.js", "package.json")


def regular_file(path):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"not a regular file: {path}")
    return path


def sha256(path):
    with regular_file(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def real_directory(path):
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise ValueError(f"not a real directory: {path}")
    return path


def electron_digest(bundle):
    """Hash paths, file bytes, executable bits and relative symlink targets.
    Excludes timestamps, ownership and extended attributes; codesign is separate.
    """
    root = real_directory(bundle).resolve()
    digest = hashlib.sha256()

    def record(*fields):
        digest.update(json.dumps(fields, ensure_ascii=True, separators=(",", ":")).encode())
        digest.update(b"\n")

    def walk(directory):
        for path in sorted(directory.iterdir(), key=lambda p: p.name):
            rel = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                target = os.readlink(path)
                if os.path.isabs(target) or not path.resolve(strict=True).is_relative_to(root):
                    raise ValueError(f"unsafe Electron symlink: {rel}")
                record(rel, "link", target)
            elif stat.S_ISDIR(mode):
                record(rel, "dir")
                walk(path)
            elif stat.S_ISREG(mode):
                record(rel, "file", mode & 0o111, sha256(path))
            else:
                raise ValueError(f"unsupported Electron entry: {rel}")

    walk(root)
    return digest.hexdigest()


def snapshot(engine, source_revision):
    if not isinstance(source_revision, str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", source_revision):
        raise ValueError("source revision must be a full lowercase Git commit ID")
    root = real_directory(Path(engine))
    app = real_directory(root / "app")
    package = json.loads(regular_file(app / "package.json").read_text(encoding="utf-8"))
    if not isinstance(package, dict) or not isinstance(package.get("version"), str) or not package["version"].strip():
        raise ValueError("Engine package version is missing")
    electron = real_directory(root / "Electron.app")
    executable = regular_file(electron / "Contents/MacOS/Electron")
    if not executable.stat().st_mode & 0o111:
        raise ValueError("Electron executable is not executable")
    return {
        "schema_version": 1,
        "source_revision": source_revision,
        "engine_package_version": package["version"],
        "app_sha256": {name: sha256(app / name) for name in APP_FILES},
        "electron_tree_sha256": electron_digest(electron),
    }


def create(engine, lock, source_revision):
    data = snapshot(engine, source_revision)
    with Path(lock).open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return data


def verify(engine, lock):
    data = json.loads(regular_file(Path(lock)).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("invalid Engine lock")
    actual = snapshot(engine, data.get("source_revision"))
    if data != actual:
        raise ValueError("Engine payload does not match lock; use the exact approved build")
    return actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("create", "verify"):
        sub = commands.add_parser(command)
        sub.add_argument("--engine", type=Path, required=True)
        sub.add_argument("--lock", type=Path, required=True)
        if command == "create":
            sub.add_argument("--source-revision", required=True)
    args = parser.parse_args()
    try:
        if args.command == "create":
            create(args.engine, args.lock, args.source_revision)
        else:
            print(verify(args.engine, args.lock)["source_revision"])
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Engine lock: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
