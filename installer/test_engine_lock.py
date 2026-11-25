#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("engine_lock", Path(__file__).with_name("engine-lock.py"))
engine_lock = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(engine_lock)


class EngineLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.engine = self.root / "Engine"
        app = self.engine / "app"
        app.mkdir(parents=True)
        for name in ("headless.cjs", "gateway-page.js"):
            (app / name).write_text("// fixture\n")
        (app / "package.json").write_text('{"version":"1.2.3"}')
        self.bundle = self.engine / "Electron.app"
        self.executable = self.bundle / "Contents/MacOS/Electron"
        self.executable.parent.mkdir(parents=True)
        self.executable.write_bytes(b"fixture")
        self.executable.chmod(0o755)
        self.link = self.bundle / "Contents/alias"
        self.link.symlink_to("MacOS/Electron")
        self.lock = self.root / "engine.lock.json"
        self.rev = "a" * 40

    def test_exact_payload_and_relocated_copy(self):
        original = engine_lock.create(self.engine, self.lock, self.rev)
        relocated = self.root / "Relocated"
        shutil.copytree(self.engine, relocated, symlinks=True)
        self.assertEqual(engine_lock.verify(relocated, self.lock), original)
        with self.assertRaises(FileExistsError):
            engine_lock.create(self.engine, self.lock, self.rev)
        self.assertEqual(engine_lock.verify(self.engine, self.lock), original)

    def test_changed_app_and_electron_files(self):
        engine_lock.create(self.engine, self.lock, self.rev)
        app = self.engine / "app/headless.cjs"
        app.write_text("changed")
        with self.assertRaisesRegex(ValueError, "does not match"):
            engine_lock.verify(self.engine, self.lock)
        app.write_text("// fixture\n")
        self.executable.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "does not match"):
            engine_lock.verify(self.engine, self.lock)

    def test_extra_bundle_file_and_mode_change(self):
        engine_lock.create(self.engine, self.lock, self.rev)
        extra = self.bundle / "extra"
        extra.write_text("extra")
        with self.assertRaisesRegex(ValueError, "does not match"):
            engine_lock.verify(self.engine, self.lock)
        extra.unlink()
        self.executable.chmod(0o700)
        with self.assertRaisesRegex(ValueError, "does not match"):
            engine_lock.verify(self.engine, self.lock)

    def test_unsafe_symlinks_and_revision(self):
        with self.assertRaisesRegex(ValueError, "full lowercase"):
            engine_lock.create(self.engine, self.lock, "unknown")
        outside = self.root / "outside"
        outside.write_text("outside")
        self.link.unlink()
        self.link.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "unsafe Electron symlink"):
            engine_lock.create(self.engine, self.lock, self.rev)
        self.assertFalse(self.lock.exists())

    def test_app_file_must_not_be_symlink(self):
        app = self.engine / "app/headless.cjs"
        app.unlink()
        app.symlink_to("gateway-page.js")
        with self.assertRaisesRegex(ValueError, "not a regular file"):
            engine_lock.create(self.engine, self.lock, self.rev)


if __name__ == "__main__":
    unittest.main()
