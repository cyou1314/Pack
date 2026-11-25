#!/usr/bin/env python3
import importlib.util
import pathlib
import shutil
import subprocess
import tempfile
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("prepare_electron", HERE / "prepare-electron.py")
prepare_electron = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare_electron)


def make_source(root, wire):
    framework = root / prepare_electron.FRAMEWORK_RELATIVE
    framework.parent.mkdir(parents=True)
    framework.write_bytes(b"prefix" + prepare_electron.MARKER + wire + b"suffix")
    return root


def fake_codesign_run(args, check):
    if args[1] == "--force":
        return subprocess.CompletedProcess(args, 0)
    if args[1] == "--verify":
        return subprocess.CompletedProcess(args, 0)
    raise AssertionError(args)


def main():
    with tempfile.TemporaryDirectory(prefix="pack-electron-prepare-test-") as tmp:
        root = pathlib.Path(tmp)
        source = make_source(root / "Source.app", bytes((1, 2, 0, ord("0"))))
        original = (source / prepare_electron.FRAMEWORK_RELATIVE).read_bytes()
        destination = root / "Encrypted.app"
        calls = []

        def successful_run(args, check):
            calls.append(args)
            if args[1] == str(source):
                shutil.copytree(args[1], args[2])
                return subprocess.CompletedProcess(args, 0)
            return fake_codesign_run(args, check)

        with mock.patch.object(prepare_electron.subprocess, "run", side_effect=successful_run):
            prepare_electron.prepare(source, destination)

        assert len(calls) == 3, calls
        assert (source / prepare_electron.FRAMEWORK_RELATIVE).read_bytes() == original
        encrypted = (destination / prepare_electron.FRAMEWORK_RELATIVE).read_bytes()
        assert encrypted.endswith(b"suffix")
        assert encrypted[len(b"prefix") + len(prepare_electron.MARKER) + 3] == ord("1")

        try:
            prepare_electron.prepare(source, destination)
        except SystemExit as exc:
            assert "already exists" in str(exc)
        else:
            raise AssertionError("existing destination was accepted")

        failed_destination = root / "Failed.app"

        def codesign_failure(args, check):
            if args[1] == str(source):
                shutil.copytree(args[1], args[2])
                return subprocess.CompletedProcess(args, 0)
            raise subprocess.CalledProcessError(1, args)

        try:
            with mock.patch.object(prepare_electron.subprocess, "run", side_effect=codesign_failure):
                prepare_electron.prepare(source, failed_destination)
        except subprocess.CalledProcessError:
            pass
        else:
            raise AssertionError("injected codesign failure was not raised")
        assert not failed_destination.exists()

        invalid_source = make_source(
            root / "Invalid.app", bytes((1, 2, 0, ord("1"))))
        invalid_destination = root / "Invalid-output.app"
        try:
            prepare_electron.prepare(invalid_source, invalid_destination)
        except SystemExit as exc:
            assert "cookie encryption state" in str(exc)
        else:
            raise AssertionError("already encrypted source was accepted")
        assert not invalid_destination.exists()

    print("PASS Electron cookie encryption preparation safety")


if __name__ == "__main__":
    main()
