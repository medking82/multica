"""Build-time only: seed public CLI files from the exact reviewed parent image."""
import os
from pathlib import Path
import shutil
import subprocess

from common import ROOT, RESULTS, BASE_ID, make_record, require, sha256

SEED = {
    'codex': ('0.156.1', Path('/opt/codex/0.156.1'), None),
    'claude': ('2.1.280', Path('/usr/local/bin/claude'),
               '1e08503dbdf3c2cb0d706d32f3408277388d1c76ef108673e8fe42c1b322925b'),
    'agy': ('1.2.8', Path('/usr/local/bin/agy'),
            'c20434f0b9278196498069dac5a0a2e72bc0b5f8aebdf17c5d535b5369b76f67'),
}


if __name__ == '__main__':
    require(os.getuid() == 0 and not ROOT.exists(), 'Seed runs once during image build only')
    subprocess.run(['python3', '/opt/runtime/verify-codex-bundle.py', '/opt/codex/0.156.1'], check=True)
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
