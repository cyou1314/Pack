#!/usr/bin/env python3
"""Read-only verification of Electron's cookie-encryption fuse."""
import mmap
import pathlib
import sys

MARKER = b"dL7pKGdnNz796PbbjQWNKmHXBZaB9tsX"


def verify(app):
    framework = pathlib.Path(app) / "Contents/Frameworks/Electron Framework.framework/Electron Framework"
    if not framework.is_file():
        raise SystemExit(f"missing Electron Framework: {framework}")
    with framework.open("rb") as stream:
        with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            pos = data.find(MARKER)
            if pos < 0 or data.find(MARKER, pos + 1) >= 0:
                raise SystemExit("unexpected cookie-encryption fuse sentinel count")
            wire = pos + len(MARKER)
            if (wire + 3 >= len(data) or data[wire] != 1
                    or data[wire + 1] < 2 or data[wire + 3] != ord("1")):
                raise SystemExit("Electron cookie encryption fuse is not enabled")
    print("PASS Electron cookie encryption fuse enabled")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: verify-electron.py /path/to/Electron.app")
    verify(sys.argv[1])
