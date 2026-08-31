"""Small on-disk contract shared by the installer, verifier and runtime launcher."""
import contextlib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time
import uuid

try:
    import fcntl
except ImportError:  # Pure contract tests also run on Windows.
    fcntl = None

ROOT = Path('/opt/agent-tools')
RESULTS = Path('/opt/validation-results')
PROVIDERS = ('codex', 'claude', 'agy')
BASE_ID = 'sha256:fdb46cfe7d838a3c7c246106ca2fa330fcb9578b915b58adb264f2d222562f94'
MAX_BYTES = 1536 * 1024**2
MAX_FILES = 128
DAY = 86400


def require(condition, message):
    if not condition:
        raise ValueError(message)


def version(value):
    require(isinstance(value, str) and len(value) <= 32 and re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value),
            'Expected a stable x.y.z version')
    return tuple(int(x) for x in value.split('.'))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 65536,
            'Missing, oversized or linked JSON record')
    return json.loads(path.read_text(encoding='utf-8'))


def sync_dir(path):
    if os.name == 'posix':
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_json(path, value):
    require(not path.is_symlink(), 'Refusing a linked record')
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    try:
        with temporary.open('xb') as stream:
            stream.write(canonical(value) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o644)
        os.replace(temporary, path)
        sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def safe_relative(name):
    require(isinstance(name, str) and len(name) <= 512 and '\\' not in name and ':' not in name
            and not any(ord(c) < 32 for c in name),
            'Invalid package path')
    path = PurePosixPath(name)
    require(not path.is_absolute() and name not in ('', '.') and
            all(x not in ('', '.', '..') for x in name.split('/')) and len(path.parts) <= 12,
            'Package path escapes its directory')
    return path


def inventory(directory):
    require(directory.is_dir() and not directory.is_symlink(), 'Invalid release directory')
    files, size = {}, 0
    for path in sorted(directory.rglob('*')):
        relative = path.relative_to(directory).as_posix()
        safe_relative(relative)
        require(not path.is_symlink(), 'Links are forbidden inside a release')
        mode = path.stat().st_mode
        if stat.S_ISDIR(mode):
            continue
        require(stat.S_ISREG(mode), 'Only regular package files are supported')
        if relative == 'release.json':
            continue
        size += path.stat().st_size
        require(len(files) < MAX_FILES and size <= MAX_BYTES, 'Package exceeds file/size bounds')
        files[relative] = {'sha256': sha256(path), 'executable': bool(mode & 0o111)}
    require(files, 'Empty release')
    return files


def check_layout(directory, provider, release_version, files):
    require(provider in PROVIDERS, 'Unknown provider')
    version(release_version)
    if provider == 'codex':
        manifest = load_json(directory / 'codex-package.json')
        require(manifest.get('version') == release_version and manifest.get('layoutVersion') == 1
                and manifest.get('target') == 'x86_64-unknown-linux-musl'
                and manifest.get('variant') == 'codex' and manifest.get('entrypoint') == 'bin/codex'
                and manifest.get('resourcesDir') == 'codex-resources'
                and manifest.get('pathDir') == 'codex-path', 'Unsupported Codex package manifest')
        required = ('bin/codex', 'bin/codex-code-mode-host', 'codex-path/rg',
                    'codex-resources/bwrap', 'codex-resources/zsh/bin/zsh')
    else:
        required = ('bin/' + provider,)
        require(set(files) == set(required), 'Unexpected native binary package layout')
    require(all(name in files and files[name]['executable'] for name in required),
            'A required executable/helper is missing')


def make_record(directory, provider, release_version, provenance):
    files = inventory(directory)
    check_layout(directory, provider, release_version, files)
    record = {'schema': 1, 'provider': provider, 'version': release_version,
              'files': files, 'provenance': provenance}
    atomic_json(directory / 'release.json', record)
    return record


def verify_release(directory, provider, release_version):
    record = load_json(directory / 'release.json')
    require(record.get('schema') == 1 and record.get('provider') == provider
            and record.get('version') == release_version, 'Release identity mismatch')
    files = inventory(directory)
    require(files == record.get('files'), 'Release checksum/inventory mismatch')
    check_layout(directory, provider, release_version, files)
    return record


def release_path(root, provider, release_version):
    require(provider in PROVIDERS, 'Unknown provider')
    version(release_version)
    result = root / provider / 'releases' / release_version
    for part in (root, root / provider, root / provider / 'releases', result):
        require(not part.is_symlink(), 'Linked tools directory is forbidden')
    return result


def current_version(root, provider):
    require(provider in PROVIDERS, 'Unknown provider')
    link = root / provider / 'current'
    require(link.is_symlink(), 'Current release must be an atomic symlink')
    target = os.readlink(link)
    require(target.startswith('releases/') and target.count('/') == 1, 'Foreign current symlink')
    release_version = target.split('/')[1]
    directory = release_path(root, provider, release_version)
    require(directory.is_dir() and (directory / 'release.json').is_file(), 'Current release is missing')
    return release_version


@contextlib.contextmanager
def lock(path, exclusive=False, nonblocking=False):
    require(fcntl is not None, 'Process locks require Linux')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode), 'Lock must be a permanent regular file')
        flags = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        if nonblocking:
            flags |= fcntl.LOCK_NB
        fcntl.flock(fd, flags)
        yield fd
    finally:
        os.close(fd)


def validation_identity():
    directory = Path(__file__).parent
    return hashlib.sha256(canonical({'base': BASE_ID, 'contract': 1,
        'common': sha256(directory / 'common.py'), 'validator': sha256(directory / 'validator.py')})).hexdigest()


def report_path(results, record):
    release_digest = hashlib.sha256(canonical(record)).hexdigest()
    return results / (release_digest + '-' + validation_identity() + '.json')


def approved_report(results, record, now=None):
    now = time.time() if now is None else now
    path = report_path(results, record)
    if not path.exists():
        return False
    report = load_json(path)
    return (report.get('passed') is True and report.get('identity') == validation_identity()
            and report.get('record_sha256') == hashlib.sha256(canonical(record)).hexdigest()
            and isinstance(report.get('checked_at'), (int, float))
            and 0 <= now - report['checked_at'] <= DAY)
