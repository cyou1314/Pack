import importlib.util
from pathlib import Path
import tempfile
import shutil
import unittest

spec = importlib.util.spec_from_file_location("browser_payload", Path(__file__).with_name("browser-payload.py"))
payload = importlib.util.module_from_spec(spec)
spec.loader.exec_module(payload)


class BrowserPayloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bundle = self.root / "payload"
        node = self.bundle / "node/bin/node"
        node.parent.mkdir(parents=True)
        node.write_bytes(b"fixture")
        node.chmod(0o755)
        runtime = self.bundle / "runtime"
        core = runtime / "browser-driver/node_modules/playwright-core"
        core.mkdir(parents=True)
        for name in payload.RUNTIME_FILES:
            (runtime / name).write_text("fixture")
        (runtime / "agent-core").mkdir()
        for name in payload.AGENT_CORE_FILES:
            (runtime / "agent-core" / name).write_text("fixture")
        driver = core.parent.parent
        for name in payload.DRIVER_FILES:
            (driver / name).write_text("fixture")
        (driver / "package.json").write_text('{"dependencies":{"playwright-core":"1.65.1"}}')
        (driver / "package-lock.json").write_text('{"packages":{"node_modules/playwright-core":{"version":"1.65.1"}}}')
        (core / "package.json").write_text('{"name":"playwright-core","version":"1.65.1"}')
        (core / "LICENSE").write_text("fixture")
        for name in payload.BROWSERS:
            folder = self.bundle / "browsers" / name
            folder.mkdir(parents=True)
            (folder / "binary").write_bytes(b"fixture")
        self.lock = self.root / "lock.json"
        self.rev = "a" * 40

    def create(self):
        return payload.create(self.bundle, self.lock, self.rev, "arm64")

    def verify(self, bundle=None, arch="arm64"):
        return payload.verify(bundle or self.bundle, self.lock, self.rev, arch)

    def test_relocation_and_no_overwrite(self):
        expected = self.create()
        self.assertFalse(expected["release_approved"])
        copy = self.root / "relocated"
        shutil.copytree(self.bundle, copy, symlinks=True)
        self.assertEqual(self.verify(copy), expected)
        with self.assertRaises(FileExistsError):
            self.create()
        with self.assertRaises(ValueError):
            self.verify(arch="x86_64")

    def test_missing_transitive_runtime_dependency(self):
        (self.bundle / "runtime/agent-core/collaboration-execution-supervisor.mjs").unlink()
        with self.assertRaises(ValueError):
            self.create()

    def test_tampered_bytes_and_executable_mode(self):
        self.create()
        node = self.bundle / "node/bin/node"
        node.write_bytes(b"modified")
        with self.assertRaises(ValueError):
            self.verify()
        node.write_bytes(b"fixture")
        node.chmod(0o644)
        with self.assertRaises(ValueError):
            self.verify()

    def test_extra_files_private_data_and_escape_symlink(self):
        self.create()
        extra = self.bundle / "browsers/chromium-1255/extra"
        extra.write_text("extra")
        with self.assertRaises(ValueError):
            self.verify()
        extra.unlink()
        private = extra.with_name("Cookies")
        private.write_text("fixture")
        with self.assertRaisesRegex(ValueError, "private data"):
            self.verify()
        private.unlink()
        extra.symlink_to(self.lock)
        with self.assertRaisesRegex(ValueError, "unsafe browser payload symlink"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
