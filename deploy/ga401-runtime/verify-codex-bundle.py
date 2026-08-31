"""Verify the pinned native package, without loading credentials or starting Codex."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
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
         "codex-resources/bwrap", "codex-package.json"}
DIRECTORIES = {"bin", "codex-path", "codex-resources"}


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
            require(mode & 0o111 == 0o111, "Package executable bits missing: " + name)
        with item.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        require(digest == hashes[name], "Package SHA-256 mismatch: " + name)
    require(json.loads((root / "codex-package.json").read_text()) == MANIFEST,
            "Unexpected Codex manifest identity")
    if entrypoint is not None:
        require(entrypoint.resolve(strict=True) == (root / MANIFEST["entrypoint"]).resolve(strict=True),
                "Codex entrypoint does not resolve inside the complete package")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--entrypoint", type=Path)
    args = parser.parse_args()
    try:
        verify_bundle(args.root, pinned_hashes(), args.entrypoint)
        print("Codex 0.151.0 complete package PASS (5 pinned files)")
    except (OSError, ValueError) as error:
        print("Codex package FAIL: " + str(error), file=sys.stderr)
        sys.exit(1)
