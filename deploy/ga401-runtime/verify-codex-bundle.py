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
    "version": "0.156.1",
    "target": "x86_64-unknown-linux-musl",
    "variant": "codex",
    "entrypoint": "bin/codex",
    "resourcesDir": "codex-resources",
    "pathDir": "codex-path",
}
def require(condition, message):
    if not condition:
        raise ValueError(message)


def pinned_hashes():
    result = {}
    for line in Path(__file__).with_name("codex-bundle.sha256").read_text().splitlines():
        digest, name = line.split()
        path = Path(name)
        require(not path.is_absolute() and ".." not in path.parts and name not in result,
                "Unexpected or duplicate package pin")
        require(len(digest) == 64 and all(char in "0123456789abcdef" for char in digest),
                "Malformed package SHA-256")
        result[name] = digest
    require(result and "bin/codex" in result and "codex-package.json" in result,
            "Incomplete package pins")
    return result


def verify_bundle(root, hashes, entrypoint=None):
    require(not root.is_symlink() and root.is_dir(), "Missing package root or symlink")
    files = set(hashes)
    directories = set()
    for name in files:
        parent = Path(name).parent
        while str(parent) != ".":
            directories.add(parent.as_posix())
            parent = parent.parent
    entries = {item.relative_to(root).as_posix(): item for item in root.rglob("*")}
    require(set(entries) == files | directories, "Incomplete or unexpected package layout")
    for name, item in entries.items():
        mode = item.lstat().st_mode
        require(not stat.S_ISLNK(mode), "Package symlink forbidden: " + name)
        if name in directories:
            require(stat.S_ISDIR(mode), "Package directory required: " + name)
            continue
        require(stat.S_ISREG(mode), "Package regular file required: " + name)
        if os.name == "posix" and Path(name).suffix not in {".json", ".md", ".txt"}:
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
    result = run("codex-resources/zsh/bin/zsh", "--version")
    require(result.returncode == 0, "Bundled zsh ABI probe failed: " + result.stderr.strip())
    print("Bundled zsh executable PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--entrypoint", type=Path)
    parser.add_argument("--probe", action="store_true", help="Run non-inference companion probes")
    args = parser.parse_args()
    try:
        hashes = pinned_hashes()
        verify_bundle(args.root, hashes, args.entrypoint)
        print(f"Codex 0.156.1 complete package PASS ({len(hashes)} pinned files)")
        if args.probe:
            probe_companions(args.root)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print("Codex package FAIL: " + str(error), file=sys.stderr)
        sys.exit(1)
