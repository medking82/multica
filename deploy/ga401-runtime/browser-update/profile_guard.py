"""Private, local-only profile backup before the first launch of a newer Chromium."""
import json
import os
from pathlib import Path
import shutil
import stat
import time
import uuid

from browser_common import chromium_version, require

LOCK_NAMES = {'SingletonLock', 'SingletonCookie', 'SingletonSocket'}


def private_directory(path):
    st = path.lstat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and not (st.st_mode & 0o077),
            'Browser profile directory must be private and owned by its user')


def private_json(path, data):
    require(not path.is_symlink(), 'Linked browser migration record')
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(data, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def profile_size(profile):
    total = 0
    for path in profile.rglob('*'):
        if path.parent == profile and path.name in LOCK_NAMES:
            continue
        st = path.lstat()
        require(stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode),
                'Unexpected linked/special file in private browser profile')
        if stat.S_ISREG(st.st_mode):
            total += st.st_size
    return total


def prepare_profile(home, record):
    """Called only at container startup, before the unchanged browser entrypoint."""
    import fcntl
    os.umask(0o077)
    private_directory(home)
    guard = home / '.browser-update.lock'
    fd = os.open(guard, os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode), 'Invalid private browser lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Keep this lease across exec and the entire browser-container lifetime.
        os.set_inheritable(fd, True)
        profile = home / 'chromium-profile'
        marker = home / 'browser-update-attempt.json'
        previous = None
        if marker.exists() or marker.is_symlink():
            require(marker.is_file() and not marker.is_symlink() and marker.stat().st_size < 65536,
                    'Invalid private browser migration marker')
            previous = json.loads(marker.read_text(encoding='utf-8'))
            require(previous.get('schema') == 1, 'Unknown profile marker schema')
            require(chromium_version(record['chromium']) >= chromium_version(previous['chromium']),
                    'Profile downgrade requires its matching backup and operator approval')
        backup = None
        if profile.exists() or profile.is_symlink():
            private_directory(profile)
            last_version = profile / 'Last Version'
            if last_version.exists() or last_version.is_symlink():
                require(last_version.is_file() and not last_version.is_symlink()
                        and last_version.stat().st_size < 64, 'Invalid profile version metadata')
                value = last_version.read_text(encoding='utf-8').strip()
                require(chromium_version(record['chromium']) >= chromium_version(value),
                        'Refusing to open a profile from a newer Chromium')
            if previous is None or previous['chromium'] != record['chromium']:
                size = profile_size(profile)
                require(shutil.disk_usage(home).free >= size + 2 * 1024**3,
                        'Insufficient private-volume space for a pre-update profile backup')
                backups = home / 'browser-update-backups'
                if not backups.exists():
                    backups.mkdir(mode=0o700)
                private_directory(backups)
                name = 'before-' + record['chromium'] + '-' + uuid.uuid4().hex
                temporary = backups / ('.incomplete-' + name)
                # Only transient Chromium singleton links are excluded. No cookie,
                # password, localStorage or IndexedDB contents leave this volume.
                def ignore(directory, names):
                    return LOCK_NAMES.intersection(names) if Path(directory) == profile else set()
                shutil.copytree(profile, temporary, symlinks=True, ignore=ignore)
                temporary.chmod(0o700)
                backup = backups / name
                os.rename(temporary, backup)
                # No automatic cleanup/rollback: preserve an incomplete copy on I/O
                # failure for the operator, and never open Chromium in that case.
        attempted = {'schema': 1, 'chromium': record['chromium'],
                     'generation': record['generation'], 'attempted_at': time.time(),
                     'backup': backup.relative_to(home).as_posix() if backup else
                         (previous.get('backup') if previous else None)}
        private_json(marker, attempted)
        return fd  # Intentionally inherited; the OS releases it at container exit.
    except BaseException:
        os.close(fd)
        raise
