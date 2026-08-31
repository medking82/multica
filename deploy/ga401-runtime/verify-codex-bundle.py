"""Verify the pinned native package; optional executable probes never run inference."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

MANIFEST = {
    "layoutVersion": 1,
    "version": "0.151.0",
    "target": "x86_64-unknown-linux-musl",
    "variant": "codex",
    "entrypoint": "bin/codex",
    "resourcesDir": "codex-resources",
    "pathDir": "codex-path",
}
FILES = {"bin/codex", "bin/codex-code-mode-host", "codex-path/rg",
         "codex-resources/bwrap", "codex-resources/zsh/bin/zsh", "codex-package.json"}
DIRECTORIES = {"bin", "codex-path", "codex-resources", "codex-resources/zsh",
               "codex-resources/zsh/bin"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def pinned_hashes():
    result = {}
    for line in Path(__file__).with_name("codex-bundle.sha256").read_text().splitlines():
        digest, name = line.split()
        require(name in FILES and name not in result, "Unexpected or duplicate package pin")
        require(len(digest) == 64 and all(char in "0123456789abcdef" for char in digest),
                "Malformed package SHA-256")
        result[name] = digest
    require(set(result) == FILES, "Incomplete package pins")
    return result


def verify_bundle(root, hashes, entrypoint=None):
    require(not root.is_symlink() and root.is_dir(), "Missing package root or symlink")
    require(set(hashes) == FILES, "Incomplete package pins")
    entries = {item.relative_to(root).as_posix(): item for item in root.rglob("*")}
    require(set(entries) == FILES | DIRECTORIES, "Incomplete or unexpected package layout")
    for name, item in entries.items():
        mode = item.lstat().st_mode
        require(not stat.S_ISLNK(mode), "Package symlink forbidden: " + name)
        if name in DIRECTORIES:
            require(stat.S_ISDIR(mode), "Package directory required: " + name)
            continue
        require(stat.S_ISREG(mode), "Package regular file required: " + name)
        if os.name == "posix" and name != "codex-package.json":
            require(mode & 0o111 and os.access(item, os.X_OK),
                    "Package is not executable by the checking user: " + name)
        with item.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        require(digest == hashes[name], "Package SHA-256 mismatch: " + name)
    require(json.loads((root / "codex-package.json").read_text()) == MANIFEST,
            "Unexpected Codex manifest identity")
    if entrypoint is not None:
        require(entrypoint.resolve(strict=True) == (root / MANIFEST["entrypoint"]).resolve(strict=True),
                "Codex entrypoint does not resolve inside the complete package")


def optional_zsh_gap(zsh_path, result, features):
    """Admit only the observed unused zsh-fork ABI gap, never an unknown failure."""
    rows = [line.split() for line in features.splitlines()
            if line.split() and line.split()[0] == "shell_zsh_fork"]
    require(rows == [["shell_zsh_fork", "under", "development", "false"]],
            "Bundled zsh failed and zsh-fork is enabled or its feature state is unknown")
    expected = {
        f"{zsh_path}: /lib/x86_64-linux-gnu/{library}.so.6: version `GLIBC_2.38' "
        f"not found (required by {zsh_path})" for library in ("libc", "libm")
    }
    require(result.returncode == 1 and not result.stdout
            and len(result.stderr.splitlines()) == 2
            and set(result.stderr.splitlines()) == expected,
            "Unexpected bundled zsh failure: " + result.stderr.strip())


def probe_companions(root):
    def run(relative_path, *arguments):
        return subprocess.run([str(root / relative_path), *arguments],
                              capture_output=True, text=True, timeout=30, check=False)

    for relative_path, argument in (("bin/codex-code-mode-host", "--help"),
                                    ("codex-path/rg", "--version"),
                                    ("codex-resources/bwrap", "--version")):
        result = run(relative_path, argument)
        require(result.returncode == 0, relative_path + " probe failed: " + result.stderr.strip())
        print(relative_path + " executable PASS")
    zsh_path = root / "codex-resources/zsh/bin/zsh"
    result = run("codex-resources/zsh/bin/zsh", "--version")
    if result.returncode == 0:
        print("Bundled zsh executable PASS")
        return
    features = run("bin/codex", "features", "list")
    require(features.returncode == 0, "Cannot determine Codex feature state")
    optional_zsh_gap(zsh_path, result, features.stdout)
    print("OPTIONAL_UNSUPPORTED: bundled zsh needs GLIBC_2.38; shell_zsh_fork is false. "
          "Default Bash/Code Mode must pass separately; do not enable zsh-fork on this base.",
          file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--entrypoint", type=Path)
    parser.add_argument("--probe", action="store_true", help="Run non-inference companion probes")
    args = parser.parse_args()
    try:
        verify_bundle(args.root, pinned_hashes(), args.entrypoint)
        print(f"Codex 0.151.0 complete package PASS ({len(FILES)} pinned files)")
        if args.probe:
            probe_companions(args.root)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print("Codex package FAIL: " + str(error), file=sys.stderr)
        sys.exit(1)
