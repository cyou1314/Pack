#!/usr/bin/env python3
import importlib.util
import pathlib
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("verify_electron", HERE / "verify-electron.py")
verify_electron = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_electron)


def make_app(root, payload):
    framework = root / "Contents/Frameworks/Electron Framework.framework/Versions/A/Electron Framework"
    framework.parent.mkdir(parents=True)
    framework.write_bytes(payload)
    return root


def main():
    marker = verify_electron.MARKER
    with tempfile.TemporaryDirectory(prefix="pack-electron-fuse-test-") as tmp:
        root = pathlib.Path(tmp) / "Electron.app"
        valid = make_app(root, b"prefix" + marker + bytes((1, 2, 0, ord("1"))) + b"suffix")
        verify_electron.verify(valid)

        bad_root = pathlib.Path(tmp) / "Electron-bad.app"
        bad = make_app(bad_root, b"prefix" + marker + bytes((0, 2, 0, ord("1"))) + b"suffix")
        try:
            verify_electron.verify(bad)
        except SystemExit as exc:
            assert "not enabled" in str(exc)
        else:
            raise AssertionError("disabled Electron fuse was accepted")

        duplicate_root = pathlib.Path(tmp) / "Electron-duplicate.app"
        duplicate = make_app(duplicate_root, marker + bytes((1, 2, 0, ord("1"))) + marker)
        try:
            verify_electron.verify(duplicate)
        except SystemExit as exc:
            assert "sentinel count" in str(exc)
        else:
            raise AssertionError("duplicate Electron fuse sentinel was accepted")

    print("PASS Electron cookie encryption fuse verifier")


if __name__ == "__main__":
    main()
