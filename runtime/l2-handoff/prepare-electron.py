#!/usr/bin/env python3
"""Prepare a separate Electron bundle with the cookie encryption fuse enabled."""
import mmap
import os
import pathlib
import shutil
import subprocess
import sys

MARKER = b"dL7pKGdnNz796PbbjQWNKmHXBZaB9tsX"
FRAMEWORK_RELATIVE = pathlib.Path(
    "Contents/Frameworks/Electron Framework.framework/Versions/A/Electron Framework"
)


def fuse_offset(framework):
    if framework.is_symlink() or not framework.is_file():
        raise SystemExit(f"Electron Framework is missing or unsafe: {framework}")
    with framework.open("rb") as stream:
        if os.fstat(stream.fileno()).st_size == 0:
            raise SystemExit("Electron Framework is empty")
        with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            pos = data.find(MARKER)
            if pos < 0 or data.find(MARKER, pos + 1) >= 0:
                raise SystemExit("unexpected fuse sentinel count")
            wire = pos + len(MARKER)
            if (wire + 3 >= len(data) or data[wire] != 1
                    or data[wire + 1] < 2 or data[wire + 3] != ord("0")):
                raise SystemExit("unexpected fuse version or cookie encryption state")
            return wire


def verify_enabled(framework):
    if framework.is_symlink() or not framework.is_file():
        raise SystemExit(f"Electron Framework is missing or unsafe: {framework}")
    with framework.open("rb") as stream:
        if os.fstat(stream.fileno()).st_size == 0:
            raise SystemExit("Electron Framework is empty")
        with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            pos = data.find(MARKER)
            if pos < 0 or data.find(MARKER, pos + 1) >= 0:
                raise SystemExit("unexpected fuse sentinel count")
            wire = pos + len(MARKER)
            if (wire + 3 >= len(data) or data[wire] != 1
                    or data[wire + 1] < 2 or data[wire + 3] != ord("1")):
                raise SystemExit("cookie encryption fuse verification failed")


def prepare(source, destination):
    source = pathlib.Path(source)
    destination = pathlib.Path(destination)
    if source.is_symlink() or not source.is_dir():
        raise SystemExit(f"source Electron.app is missing or unsafe: {source}")
    if destination.exists() or destination.is_symlink():
        raise SystemExit(f"destination already exists: {destination}")
    if not destination.parent.is_dir():
        raise SystemExit(f"destination parent directory is missing: {destination.parent}")
    source_real = source.resolve()
    destination_path = destination.parent.resolve() / destination.name
    if destination_path == source_real or source_real in destination_path.parents:
        raise SystemExit("destination must be outside the source Electron.app")

    source_framework = source / FRAMEWORK_RELATIVE
    wire = fuse_offset(source_framework)
    created = False
    try:
        subprocess.run(["/usr/bin/ditto", str(source), str(destination)], check=True)
        created = True
        framework = destination / FRAMEWORK_RELATIVE
        if framework.is_symlink() or not framework.is_file():
            raise SystemExit("copied Electron Framework is missing or unsafe")
        with framework.open("r+b") as stream:
            stream.seek(wire + 3)
            stream.write(b"1")
            stream.flush()
            os.fsync(stream.fileno())
        subprocess.run(
            ["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(destination)],
            check=True,
        )
        subprocess.run(
            ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(destination)],
            check=True,
        )
        verify_enabled(framework)
        print("isolated Electron bundle: cookieEncryption enabled and signature verified")
    except BaseException:
        if created and destination.exists() and not destination.is_symlink():
            shutil.rmtree(destination)
        raise


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: prepare-electron.py SOURCE_Electron.app DESTINATION_Electron.app")
    prepare(sys.argv[1], sys.argv[2])
