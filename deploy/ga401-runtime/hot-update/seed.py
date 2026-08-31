"""Build-time only: seed public CLI files from the exact reviewed parent image."""
import os
from pathlib import Path
import shutil
import subprocess

from common import ROOT, RESULTS, BASE_ID, make_record, require, sha256

SEED = {
    'codex': ('0.151.0', Path('/opt/codex/0.151.0'), None),
    'claude': ('2.1.251', Path('/usr/local/bin/claude'),
               'fd5f10ff0eb58daec04900466b143ea98aab50abf208a422bc008eaec13f61f7'),
    'agy': ('1.1.22', Path('/usr/local/bin/agy'),
            '2822292f90deea4556938a8728fe4ed02a1d66d1525cf75fa07a171e36a38c25'),
}


if __name__ == '__main__':
    require(os.getuid() == 0 and not ROOT.exists(), 'Seed runs once during image build only')
    subprocess.run(['python3', '/opt/runtime/verify-codex-bundle.py', '/opt/codex/0.151.0'], check=True)
    ROOT.mkdir(mode=0o755)
    for provider, (v, source, digest) in SEED.items():
        parent = ROOT / provider
        destination = parent / 'releases' / v
        destination.parent.mkdir(parents=True)
        if provider == 'codex':
            shutil.copytree(source, destination)
        else:
            require(sha256(source) == digest, 'Seed binary differs from reviewed pin')
            (destination / 'bin').mkdir(parents=True)
            shutil.copy2(source, destination / 'bin' / provider)
        make_record(destination, provider, v, {'seed_image': BASE_ID})
        (parent / 'current').symlink_to('releases/' + v)
        (parent / 'in-use.lock').touch(mode=0o444)
    (ROOT / 'updater.lock').touch(mode=0o444)
    for path in [ROOT, *ROOT.rglob('*')]:
        os.chown(path, 1001, 1001, follow_symlinks=False)
        if path.is_file() and not path.is_symlink():
            path.chmod(0o555 if path.stat().st_mode & 0o111 else 0o444)
    RESULTS.mkdir(mode=0o755)
    os.chown(RESULTS, 1002, 1002)
