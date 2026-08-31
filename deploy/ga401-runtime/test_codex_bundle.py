import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("bundle", Path(__file__).with_name("verify-codex-bundle.py"))
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


class CodexBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "package"
        self.hashes = {}
        for name in bundle.FILES:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            data = json.dumps(bundle.MANIFEST).encode() if name == "codex-package.json" else name.encode()
            target.write_bytes(data)
            target.chmod(0o755)
            self.hashes[name] = hashlib.sha256(data).hexdigest()

    def verify(self, entrypoint=None):
        bundle.verify_bundle(self.root, self.hashes, entrypoint)

    def test_complete_layout(self):
        self.verify(self.root / "bin/codex")

    def test_production_pins_cover_all_files(self):
        self.assertEqual(set(bundle.pinned_hashes()), bundle.FILES)

    def test_rejects_missing_helper_regression(self):
        (self.root / "bin/codex-code-mode-host").unlink()
        with self.assertRaisesRegex(ValueError, "layout"):
            self.verify()

    def test_rejects_missing_bundled_resources(self):
        for name in ("codex-path/rg", "codex-resources/bwrap", "codex-package.json"):
            with self.subTest(name=name):
                path = self.root / name
                saved = path.read_bytes()
                path.unlink()
                with self.assertRaisesRegex(ValueError, "layout"):
                    self.verify()
                path.write_bytes(saved)
                path.chmod(0o755)

    def test_rejects_single_binary_snapshot(self):
        with self.assertRaisesRegex(ValueError, "root"):
            bundle.verify_bundle(self.root / "bin/codex", self.hashes)

    def test_rejects_mixed_or_corrupted_version(self):
        (self.root / "bin/codex-code-mode-host").write_bytes(b"different version")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.verify()

    def test_rejects_other_manifest_identity(self):
        data = json.dumps({**bundle.MANIFEST, "version": "0.152.0"}).encode()
        (self.root / "codex-package.json").write_bytes(data)
        self.hashes["codex-package.json"] = hashlib.sha256(data).hexdigest()
        with self.assertRaisesRegex(ValueError, "manifest"):
            self.verify()

    def test_rejects_unexpected_auth_or_settings_file(self):
        (self.root / "auth.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "layout"):
            self.verify()

    def test_rejects_unbundled_entrypoint(self):
        external = Path(self.temp.name) / "codex"
        external.write_bytes((self.root / "bin/codex").read_bytes())
        with self.assertRaisesRegex(ValueError, "entrypoint"):
            self.verify(external)

    @unittest.skipUnless(os.name == "posix", "Linux executable mode and symlink contract")
    def test_accepts_cli_symlink_into_bundle(self):
        external = Path(self.temp.name) / "codex"
        external.symlink_to(self.root / "bin/codex")
        self.verify(external)

    @unittest.skipUnless(os.name == "posix", "Linux executable mode and symlink contract")
    def test_rejects_non_executable_helper(self):
        (self.root / "bin/codex-code-mode-host").chmod(0o644)
        with self.assertRaisesRegex(ValueError, "executable"):
            self.verify()

    @unittest.skipUnless(os.name == "posix", "Linux executable mode and symlink contract")
    def test_rejects_package_symlink(self):
        target = self.root / "bin/codex-code-mode-host"
        target.unlink()
        target.symlink_to("codex")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
