import hashlib
import importlib.util
import json
import os
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


class OptionalZshCompatibilityTests(unittest.TestCase):
    zsh_path = Path("/opt/codex/0.151.0/codex-resources/zsh/bin/zsh")
    disabled = "shell_zsh_fork under development false\nunified_exec_zsh_fork removed true\n"

    def result(self, returncode=1, stdout="", extra=""):
        stderr = "\n".join(
            f"{self.zsh_path}: /lib/x86_64-linux-gnu/{library}.so.6: version `GLIBC_2.38' "
            f"not found (required by {self.zsh_path})" for library in ("libm", "libc")) + "\n" + extra
        return CompletedProcess([], returncode, stdout, stderr)

    def test_known_unused_abi_gap_is_explicitly_classified(self):
        bundle.optional_zsh_gap(self.zsh_path, self.result(), self.disabled)

    def test_enabled_zsh_fork_never_bypasses_failure(self):
        with self.assertRaisesRegex(ValueError, "enabled or its feature state is unknown"):
            bundle.optional_zsh_gap(self.zsh_path, self.result(), self.disabled.replace("false", "true"))

    def test_missing_ambiguous_or_changed_feature_state_fails_closed(self):
        for state in ("", self.disabled * 2, self.disabled.replace("under development", "stable")):
            with self.subTest(state=state), self.assertRaises(ValueError):
                bundle.optional_zsh_gap(self.zsh_path, self.result(), state)

    def test_other_loader_failure_is_not_ignored(self):
        for result in (self.result(returncode=127), self.result(stdout="unexpected"),
                       self.result(extra="another failure\n"),
                       CompletedProcess([], 1, "", "Permission denied")):
            with self.subTest(result=result), self.assertRaisesRegex(ValueError, "Unexpected"):
                bundle.optional_zsh_gap(self.zsh_path, result, self.disabled)

    def test_mandatory_helper_failure_stops_the_probe(self):
        with patch.object(bundle.subprocess, "run", return_value=CompletedProcess([], 1, "", "missing")) as run:
            with self.assertRaisesRegex(ValueError, "codex-code-mode-host probe failed"):
                bundle.probe_companions(Path("/opt/codex/0.151.0"))
            self.assertEqual(run.call_count, 1)

    def test_probe_reports_known_gap_and_does_not_override_features(self):
        ok = CompletedProcess([], 0, "version", "")
        with patch.object(bundle.subprocess, "run", side_effect=[ok, ok, ok, self.result(),
                          CompletedProcess([], 0, self.disabled, "")]) as run:
            bundle.probe_companions(Path("/opt/codex/0.151.0"))
            self.assertEqual(run.call_args.args[0][1:], ["features", "list"])
            self.assertEqual(run.call_count, 5)

    def test_failed_feature_probe_never_assumes_disabled(self):
        ok = CompletedProcess([], 0, "version", "")
        with patch.object(bundle.subprocess, "run", side_effect=[ok, ok, ok, self.result(),
                          CompletedProcess([], 1, "", "failed")]):
            with self.assertRaisesRegex(ValueError, "Cannot determine"):
                bundle.probe_companions(Path("/opt/codex/0.151.0"))


if __name__ == "__main__":
    unittest.main()
