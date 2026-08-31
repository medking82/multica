"""Image build only: copy public browser packages, never an account/profile volume."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from browser_common import ROOT, BASES, core_path, generation, package_environment, require, write_record


def seed():
    require(os.getuid() == 0 and not ROOT.exists(), 'Browser seed is build-time only')
    name = generation('1.62.1', '0.0.79')
    directory = ROOT / 'releases' / name
    directory.mkdir(parents=True)
    for package in ('playwright', '@playwright/mcp'):
        shutil.copytree(Path('/usr/local/lib/node_modules') / package,
                        directory / 'node_modules' / package,
                        ignore=shutil.ignore_patterns('.bin'))
    shutil.copytree('/opt/playwright-browsers', core_path(directory) / '.local-browsers',
                    ignore=shutil.ignore_patterns('.links'))
    with tempfile.TemporaryDirectory(prefix='browser-seed-') as temporary:
        result = subprocess.run(['node', '-e', 'process.stdout.write(require("playwright").chromium.executablePath())'],
            cwd=temporary, env=package_environment(directory, Path(temporary)), capture_output=True, text=True, check=True)
    executable = Path(result.stdout).relative_to(directory).as_posix()
    write_record(directory, name, executable, {'seed_image': BASES['runtime']})
    (ROOT / 'current').symlink_to('releases/' + name)
    (ROOT / 'updater.lock').touch(mode=0o444)
    for path in [ROOT, *ROOT.rglob('*')]:
        os.chown(path, 1001, 1001, follow_symlinks=False)
        if path.is_file() and not path.is_symlink():
            path.chmod(0o555 if path.stat().st_mode & 0o111 else 0o444)


if __name__ == '__main__':
    seed()
