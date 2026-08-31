"""Immutable Playwright/MCP/Chromium generations shared by two real consumers."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'hot-update'))
from common import atomic_json, canonical, lock, require, sha256, sync_dir, version

ROOT = Path('/opt/browser-tools')
RESULTS = Path('/opt/validation-results')
BASES = {
    'runtime': 'sha256:d69f3f5f17ae3c19f731e6a8dd0c915497c93629920b32716f9a8cb15b2e3082',
    'login': 'sha256:cebf16fb1624877aa17d5a3232a475fadb01c60c20f6937b680426c789cd9edc',
}
DAY = 86400
MAX_FILES = 12000
MAX_BYTES = 3 * 1024**3
MAX_JSON = 4 * 1024**2


def safe_relative(name):
    # Nested MCP -> Playwright -> core packages exceed the standalone CLI's
    # 12-component bound. Keep the same traversal/character protections, with a
    # bounded depth that also covers the three reviewed npm dependency levels.
    require(isinstance(name, str) and len(name) <= 512 and '\\' not in name and ':' not in name
            and not any(ord(c) < 32 for c in name), 'Invalid browser package path')
    path = PurePosixPath(name)
    require(not path.is_absolute() and name not in ('', '.') and
            all(x not in ('', '.', '..') for x in name.split('/')) and len(path.parts) <= 32,
            'Browser package path escapes its directory or exceeds its depth bound')
    return path


def read_json(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_JSON,
            'Missing, linked or oversized browser record')
    return json.loads(path.read_text(encoding='utf-8'))


def generation(pw, mcp):
    version(pw)
    version(mcp)
    return 'pw-' + pw + '-mcp-' + mcp


def parse_generation(value):
    require(isinstance(value, str), 'Invalid browser generation')
    match = re.fullmatch(r'pw-([0-9.]+)-mcp-([0-9.]+)', value)
    require(match is not None, 'Invalid browser generation')
    pw, mcp = match.groups()
    version(pw)
    version(mcp)
    return pw, mcp


def chromium_version(value):
    require(isinstance(value, str) and re.fullmatch(r'[0-9]+(?:\.[0-9]+){3}', value),
            'Invalid Chromium version')
    return tuple(map(int, value.split('.')))


def newer(candidate, current):
    before, after = parse_generation(current), parse_generation(candidate)
    return all(version(b) >= version(a) for a, b in zip(before, after)) and candidate != current


def release_path(root, name):
    parse_generation(name)
    path = root / 'releases' / name
    for item in (root, root / 'releases', path):
        require(not item.is_symlink(), 'Linked browser release root')
    return path


def selected(root=ROOT):
    pointer = root / 'current'
    require(pointer.is_symlink(), 'Missing browser current pointer')
    target = os.readlink(pointer)
    require(target.startswith('releases/') and target.count('/') == 1,
            'Browser pointer escapes releases')
    path = release_path(root, target.split('/')[1])
    require(path.is_dir() and (path / 'release.json').is_file(), 'Missing browser generation')
    return path


def inventory(directory):
    require(directory.is_dir() and not directory.is_symlink(), 'Unsafe browser directory')
    files, size = {}, 0
    for path in sorted(directory.rglob('*')):
        name = path.relative_to(directory).as_posix()
        safe_relative(name)
        mode = path.lstat().st_mode
        require(not stat.S_ISLNK(mode), 'Browser releases must not contain symlinks')
        if stat.S_ISDIR(mode):
            continue
        require(stat.S_ISREG(mode), 'Non-regular browser package file')
        if name == 'release.json':
            continue
        size += path.stat().st_size
        require(len(files) < MAX_FILES and size <= MAX_BYTES, 'Browser package exceeds bounds')
        files[name] = {'sha256': sha256(path), 'executable': bool(mode & 0o111)}
    require(files, 'Empty browser package')
    return files


def core_path(directory):
    # Same Node resolution in the inherited global layout and nested package install.
    return directory / 'node_modules/playwright/node_modules/playwright-core'


def metadata(directory, name):
    pw, mcp = parse_generation(name)
    for relative, expected, v in (
        ('node_modules/playwright', 'playwright', pw),
        ('node_modules/playwright/node_modules/playwright-core', 'playwright-core', pw),
        ('node_modules/@playwright/mcp', '@playwright/mcp', mcp),
    ):
        package = read_json(directory / relative / 'package.json')
        require(package.get('name') == expected and package.get('version') == v,
                'Browser package identity mismatch')
        require((directory / relative / 'cli.js').is_file(), 'Missing package CLI')
    browsers = read_json(core_path(directory) / 'browsers.json')['browsers']
    by_name = {item['name']: item for item in browsers}
    chromium = by_name['chromium']
    shell = by_name['chromium-headless-shell']
    ffmpeg = by_name['ffmpeg']
    v = chromium['browserVersion']
    chromium_version(v)
    require(shell['browserVersion'] == v and shell['revision'] == chromium['revision'],
            'Mixed Chromium/headless revisions')
    for item in (chromium, shell, ffmpeg):
        require(isinstance(item['revision'], str) and re.fullmatch('[0-9]+', item['revision']),
                'Invalid browser revision')
    return {'generation': name, 'playwright': pw, 'mcp': mcp, 'chromium': v,
            'revision': chromium['revision'], 'ffmpeg_revision': ffmpeg['revision']}


def check_layout(directory, record):
    info = metadata(directory, record['generation'])
    require(all(record.get(k) == v for k, v in info.items()), 'Browser metadata mismatch')
    relative = record.get('executable')
    safe_relative(relative)
    executable = directory / relative
    require(executable.is_relative_to(core_path(directory) / '.local-browsers')
            and executable.name == 'chrome' and executable.is_file() and not executable.is_symlink(),
            'Unexpected Chromium executable')
    require(relative in record['files'] and record['files'][relative]['executable'],
            'Chromium is not executable')
    return record


def verify_release(directory):
    record = read_json(directory / 'release.json')
    require(record.get('schema') == 1, 'Unknown browser record schema')
    check_layout(directory, record)
    require(record['files'] == inventory(directory), 'Browser inventory/checksum mismatch')
    return record


def write_record(directory, name, executable, provenance):
    record = {'schema': 1, **metadata(directory, name), 'executable': executable,
              'provenance': provenance, 'files': inventory(directory)}
    check_layout(directory, record)
    atomic_json(directory / 'release.json', record)
    return record


def validation_identity(consumer):
    require(consumer in BASES, 'Unknown browser consumer')
    here = Path(__file__).resolve().parent
    sources = ['browser_common.py', 'browser_validator.py', 'browser_probe.cjs',
               'browser_launcher.py', 'profile_guard.py']
    return hashlib.sha256(canonical({'base': BASES[consumer], 'consumer': consumer,
        'contract': 1, 'source': {name: sha256(here / name) for name in sources}})).hexdigest()


def report_path(results, record, consumer):
    digest = hashlib.sha256(canonical(record)).hexdigest()
    return results / ('browser-' + digest + '-' + validation_identity(consumer) + '.json')


def approved(results, record, consumer, now=None):
    now = time.time() if now is None else now
    path = report_path(results, record, consumer)
    if not path.exists():
        return False
    report = read_json(path)
    checked = report.get('checked_at')
    return (report.get('passed') is True and report.get('consumer') == consumer
            and report.get('identity') == validation_identity(consumer)
            and report.get('record_sha256') == hashlib.sha256(canonical(record)).hexdigest()
            and isinstance(checked, (int, float)) and 0 <= now - checked <= DAY)


def package_environment(directory, home):
    return {'HOME': str(home), 'TMPDIR': str(home), 'PATH': '/usr/local/bin:/usr/bin:/bin',
            'LANG': 'C.UTF-8', 'NODE_PATH': str(directory / 'node_modules'),
            'PLAYWRIGHT_BROWSERS_PATH': '0', 'PLAYWRIGHT_SKIP_BROWSER_GC': '1',
            'NO_COLOR': '1', 'PYTHONUTF8': '1'}
