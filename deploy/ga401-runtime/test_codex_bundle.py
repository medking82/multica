import hashlib
import importlib.util
import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from subprocess import CompletedProcess
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("bundle", Path(__file__).with_name("verify-codex-bundle.py"))
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


class CodexBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "package"
        self.hashes = {}
        for name in bundle.pinned_hashes():
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
        pins = bundle.pinned_hashes()
        self.assertEqual(len(pins), 44)
        self.assertIn("codex-resources/voice/bin/codex-voice-host", pins)
        self.assertIn("codex-resources/zsh/bin/zsh", pins)

    def test_rejects_missing_voice_companion(self):
        (self.root / "codex-resources/voice/bin/codex-voice-host").unlink()
        with self.assertRaisesRegex(ValueError, "layout"):
            self.verify()

    def test_rejects_missing_helper_regression(self):
        (self.root / "bin/codex-code-mode-host").unlink()
        with self.assertRaisesRegex(ValueError, "layout"):
            self.verify()

    def test_rejects_missing_bundled_resources(self):
        for name in ("codex-path/rg", "codex-resources/bwrap", "codex-resources/zsh/bin/zsh", "codex-package.json"):
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

    @unittest.skipUnless(os.name == "posix", "Linux owner-only source package permissions")
    def test_accepts_private_source_executable_for_its_owner(self):
        (self.root / "bin/codex").chmod(0o700)
        self.verify()

    def test_refuses_source_directory_with_an_unpinned_companion(self):
        (self.root / "bin/new-companion").write_bytes(b"not in the pinned inventory")
        with self.assertRaisesRegex(ValueError, "layout"):
            self.verify()

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


class CompanionProbeTests(unittest.TestCase):
    def test_mandatory_helper_failure_stops_the_probe(self):
        with patch.object(bundle.subprocess, "run", return_value=CompletedProcess([], 1, "", "missing")) as run:
            with self.assertRaisesRegex(ValueError, "codex-code-mode-host probe failed"):
                bundle.probe_companions(Path("/opt/codex/0.156.1"))
            self.assertEqual(run.call_count, 1)

    def test_zsh_abi_failure_is_fatal(self):
        ok = CompletedProcess([], 0, "version", "")
        failed = CompletedProcess([], 1, "", "loader failure")
        with patch.object(bundle.subprocess, "run", side_effect=[ok, ok, ok, failed]):
            with self.assertRaisesRegex(ValueError, "Bundled zsh ABI probe failed"):
                bundle.probe_companions(Path("/opt/codex/0.156.1"))

    def test_all_companions_probe_success(self):
        ok = CompletedProcess([], 0, "version", "")
        with patch.object(bundle.subprocess, "run", return_value=ok) as run:
            bundle.probe_companions(Path("/opt/codex/0.156.1"))
            self.assertEqual(run.call_count, 4)


@unittest.skipUnless(os.name == "posix", "Linux staging and symlink contract")
class PackageDirectoryStagingTests(unittest.TestCase):
    def run_prepare(self, root):
        source = Path(__file__).with_name("prepare-assets.sh").read_text()
        body = source.split("prepare_package_directories() {", 1)[1].split("\n}", 1)[0]
        script = "set -eu\numask 077\nprepare_package_directories() {" + body + "\n}\n"
        script += "prepare_package_directories .assets/codex-bundle/codex-resources/zsh/bin\n"
        return subprocess.run(["bash", "-c", script], cwd=root, capture_output=True, text=True)

    def test_fresh_staging_opens_every_package_ancestor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".assets").mkdir(mode=0o700)
            result = self.run_prepare(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            path = root / ".assets/codex-bundle/codex-resources/zsh/bin"
            while path != root / ".assets":
                self.assertEqual(path.stat().st_mode & 0o777, 0o755, str(path))
                path = path.parent
            self.assertEqual((root / ".assets").stat().st_mode & 0o777, 0o700)

    def test_linked_ancestor_is_refused_before_external_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".assets").mkdir()
            external = root / "external"
            external.mkdir()
            (root / ".assets/codex-bundle").symlink_to(external, target_is_directory=True)
            result = self.run_prepare(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Refusing symlinked package directory", result.stderr)
            self.assertEqual(list(external.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
