"""Daily public-package acquisition, dual offline validation, next-session promotion."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import zipfile

from browser_common import (ROOT, RESULTS, BASES, DAY, MAX_BYTES, MAX_FILES, approved,
    atomic_json, core_path, generation, inventory, lock, metadata, newer,
    package_environment, parse_generation, read_json, release_path, report_path,
    require, safe_relative, selected, sha256, sync_dir, verify_release, version, write_record,
    chromium_version)

NPM = 'https://registry.npmjs.org/'
PACKAGES = {'playwright', 'playwright-core', '@playwright/mcp'}
PACKAGE_VERSION = r'[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?'
MAX_DOWNLOAD = 1024**3


def approved_url(url):
    p = urlsplit(url)
    require(p.scheme == 'https' and not p.username and not p.password and
            p.port in (None, 443) and not p.query and not p.fragment,
            'Only public publisher HTTPS URLs are supported')
    paths = {
        'registry.npmjs.org': r'/(?:playwright(?:-core)?|@playwright/mcp)/(?:latest|' +
            PACKAGE_VERSION + r'|-/[a-z-]+-' + PACKAGE_VERSION + r'\.tgz)',
        'cdn.playwright.dev': r'/(?:builds/cft/[0-9.]+/linux64/chrome(?:-headless-shell)?-linux64\.zip|'
            r'(?:dbazure/download/playwright/)?builds/ffmpeg/[0-9]+/ffmpeg-linux\.zip)',
        'playwright.download.prss.microsoft.com':
            r'/dbazure/download/playwright/builds/ffmpeg/[0-9]+/ffmpeg-linux\.zip',
        'storage.googleapis.com':
            r'/chrome-for-testing-public/[0-9.]+/linux64/chrome(?:-headless-shell)?-linux64\.zip',
    }
    require(p.hostname in paths and re.fullmatch(paths[p.hostname], p.path),
            'Browser artifact URL is outside the approved publisher paths')
    return url


class Redirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        approved_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, destination=None, limit=512 * 1024):
    opener = build_opener(ProxyHandler({}), Redirects())
    started, size, chunks = time.monotonic(), 0, []
    with opener.open(Request(approved_url(url), headers={'User-Agent': 'GA401-Browser-Updater/1'}),
                     timeout=30) as response:
        approved_url(response.geturl())
        header = response.headers.get('Content-Length')
        declared = None
        if header is not None:
            require(re.fullmatch(r'[0-9]{1,20}', header.strip()), 'Invalid download content length')
            declared = int(header)
            require(declared <= limit, 'Download content length exceeds bound')
        with destination.open('xb') if destination else tempfile.TemporaryFile() as output:
            while True:
                require(time.monotonic() - started < 600, 'Browser download deadline exceeded')
                chunk = response.read(min(1024**2, limit + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                require(size <= limit, 'Browser download exceeds bound')
                output.write(chunk)
                if destination is None:
                    chunks.append(chunk)
            require(declared is None or size == declared, 'Browser download is truncated')
            output.flush()
            if destination:
                os.fsync(output.fileno())
    return b''.join(chunks)


def package_metadata(name, v):
    require(name in PACKAGES and (v == 'latest' or re.fullmatch(PACKAGE_VERSION, v)),
            'Unapproved npm package/version')
    data = json.loads(fetch(NPM + name + '/' + v))
    require(data.get('name') == name and isinstance(data.get('version'), str)
            and re.fullmatch(PACKAGE_VERSION, data['version'])
            and (v == 'latest' or data['version'] == v), 'npm identity mismatch')
    if v == 'latest':
        version(data['version'])  # Never automatically select a root prerelease.
    dist = data['dist']
    approved_url(dist['tarball'])
    require(dist['tarball'].startswith(NPM + name + '/-/'), 'Foreign npm tarball owner')
    sri = dist.get('integrity', '')
    require(sri.startswith('sha512-') and len(base64.b64decode(sri[7:], validate=True)) == 64,
            'npm SHA512 integrity is required')
    deps = data.get('dependencies', {})
    require(isinstance(deps, dict) and set(deps) <= PACKAGES
            and all(isinstance(x, str) and re.fullmatch(PACKAGE_VERSION, x) for x in deps.values()),
            'New/ranged npm dependencies require maintenance review')
    require(not data.get('peerDependencies') and
            set(data.get('optionalDependencies', {})) <= {'fsevents'}, 'New npm dependency surface')
    return data


def discover(current):
    pw = package_metadata('playwright', 'latest')['version']
    mcp = package_metadata('@playwright/mcp', 'latest')['version']
    # A publisher stable tag moving backwards never downgrades the other component.
    before = parse_generation(current)
    pw, mcp = (max((a, b), key=version) for a, b in zip(before, (pw, mcp)))
    return generation(pw, mcp)


def extract_tar(payload, destination):
    seen, size = set(), 0
    with tarfile.open(payload, 'r:gz') as archive:
        for item in archive:
            name = item.name.rstrip('/')
            safe_relative(name)
            require(name not in seen and len(seen) < MAX_FILES, 'Duplicate/oversized npm archive')
            seen.add(name)
            require(item.isdir() or item.isfile(), 'npm links/devices are forbidden')
            require(name == 'package' or name.startswith('package/'), 'Unexpected npm archive root')
            if item.isdir():
                continue
            size += item.size
            require(0 <= item.size <= 128 * 1024**2 and size <= 256 * 1024**2,
                    'npm archive exceeds bounds')
            relative = name[len('package/'):]
            safe_relative(relative)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(item) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output)
                output.flush()
                os.fsync(output.fileno())
            target.chmod(0o555 if item.mode & 0o111 else 0o444)


def install_package(name, v, destination, temporary, provenance, depth=0):
    require(depth <= 2 and len(provenance) < 8, 'npm dependency graph exceeds the reviewed shape')
    data = package_metadata(name, v)
    payload = temporary / ('npm-' + str(len(provenance)) + '.tgz')
    fetch(data['dist']['tarball'], payload, limit=128 * 1024**2)
    digest = hashlib.sha512(payload.read_bytes()).digest()
    require(digest == base64.b64decode(data['dist']['integrity'][7:], validate=True),
            'npm package integrity mismatch')
    destination.mkdir(parents=True)
    extract_tar(payload, destination)
    actual = read_json(destination / 'package.json')
    require(all(actual.get(k) == data.get(k) for k in
                ('name', 'version', 'dependencies', 'optionalDependencies', 'peerDependencies')),
            'npm archive/registry metadata differ')
    provenance.append({'name': name, 'version': v, 'url': data['dist']['tarball'],
                       'integrity': data['dist']['integrity']})
    for child, child_v in sorted(data.get('dependencies', {}).items()):
        install_package(child, child_v, destination / 'node_modules' / child,
                        temporary, provenance, depth + 1)


def download_plan(text, directory, info):
    """Consume the native dry-run contract; reject new platforms/paths/artifacts."""
    expected = {'chromium-' + info['revision'], 'chromium_headless_shell-' + info['revision'],
                'ffmpeg-' + info['ffmpeg_revision']}
    plan, location = [], None
    for line in text.splitlines():
        if line.strip().startswith('Install location:'):
            location = Path(line.split(':', 1)[1].strip())
        elif line.strip().startswith('Download url:'):
            require(location is not None and location.parent == core_path(directory) / '.local-browsers'
                    and location.name in expected, 'Unexpected browser install location')
            url = approved_url(line.split(':', 1)[1].strip())
            if not location.name.startswith('ffmpeg-'):
                require('/' + info['chromium'] + '/linux64/' in url,
                        'Browser artifact version mismatch')
            plan.append((location, url))
            location = None
    require(len(plan) == 3 and {p.name for p, _ in plan} == expected,
            'Incomplete/mixed browser download plan')
    return plan


def extract_zip(payload, destination):
    seen, size = set(), 0
    with zipfile.ZipFile(payload) as archive:
        for item in archive.infolist():
            name = item.filename.rstrip('/')
            safe_relative(name)
            require(name not in seen and len(seen) < MAX_FILES, 'Duplicate/oversized browser archive')
            seen.add(name)
            mode = item.external_attr >> 16
            require(stat.S_IFMT(mode) in (0, stat.S_IFREG, stat.S_IFDIR),
                    'Browser archive links/devices are forbidden')
            if item.is_dir():
                continue
            size += item.file_size
            require(0 <= item.file_size <= MAX_DOWNLOAD and size <= MAX_BYTES,
                    'Expanded browser archive exceeds bounds')
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output, 1024**2)
                output.flush()
                os.fsync(output.fileno())
            # Never preserve publisher setuid/setgid bits.
            target.chmod(0o555 if mode & 0o111 else 0o444)


def run_description(directory, home, args):
    result = subprocess.run(['node', str(directory / 'node_modules/playwright/cli.js'), *args],
        env=package_environment(directory, home), cwd=home, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=30, check=False)
    require(result.returncode == 0 and len(result.stdout) < 65536,
            'Playwright package cannot describe its browser installation')
    return result.stdout


def install(root, name):
    destination = release_path(root, name)
    if destination.exists():
        return verify_release(destination)
    require(shutil.disk_usage(root).free >= 6 * 1024**3, 'Less than 6 GiB free; browser update held')
    with tempfile.TemporaryDirectory(prefix='.download-', dir=root) as staging:
        temporary = Path(staging)
        directory = temporary / 'bundle'
        directory.mkdir()
        home = temporary / 'home'
        home.mkdir(mode=0o700)
        provenance = {'npm': [], 'browsers': []}
        pw, mcp = parse_generation(name)
        install_package('playwright', pw, directory / 'node_modules/playwright', temporary, provenance['npm'])
        install_package('@playwright/mcp', mcp, directory / 'node_modules/@playwright/mcp', temporary, provenance['npm'])
        info = metadata(directory, name)
        plan = download_plan(run_description(directory, home, ['install', '--dry-run', 'chromium']), directory, info)
        for i, (target, url) in enumerate(plan):
            payload = temporary / ('browser-' + str(i) + '.zip')
            fetch(url, payload, limit=MAX_DOWNLOAD)
            target.mkdir(parents=True)
            extract_zip(payload, target)
            (target / 'INSTALLATION_COMPLETE').touch(mode=0o444)
            provenance['browsers'].append({'url': url, 'sha256': sha256(payload),
                                          'trust': 'publisher-https; local-sha256-not-publisher-signature'})
        # The documented native API owns the executable path, not a guessed revision layout.
        result = subprocess.run(['node', '-e', 'process.stdout.write(require("playwright").chromium.executablePath())'],
            env=package_environment(directory, home), cwd=home, capture_output=True, text=True, timeout=30)
        require(result.returncode == 0, 'Cannot resolve installed Chromium')
        executable = Path(result.stdout).relative_to(directory).as_posix()
        record = write_record(directory, name, executable, provenance)
        # The updater owns directories; consumers only ever mount this tree read-only.
        for file in directory.rglob('*'):
            if file.is_file():
                file.chmod(0o555 if file.stat().st_mode & 0o111 else 0o444)
        os.rename(directory, destination)
        sync_dir(destination.parent)
        return record


def promote(root, results, name, now=None):
    current = selected(root)
    require(newer(name, current.name), 'Browser downgrade/reinstall is forbidden')
    directory = release_path(root, name)
    record = read_json(directory / 'release.json')
    require(chromium_version(record['chromium']) >= chromium_version(read_json(current / 'release.json')['chromium']),
            'Chromium downgrade is forbidden')
    for consumer in BASES:
        if not approved(results, record, consumer, now):
            path = report_path(results, record, consumer)
            if path.exists() and read_json(path).get('passed') is False:
                raise ValueError(consumer + ' browser validation failed; holding current')
            return 'awaiting-' + consumer + '-validation'
    require(record == verify_release(directory), 'Browser package changed before promotion')
    require(all(approved(results, record, consumer, now) for consumer in BASES), 'Browser reports changed')
    require(selected(root) == current, 'Browser current changed during promotion')
    next_path = root / 'current.next'
    require(not next_path.exists() and not next_path.is_symlink(), 'Unexpected browser pending pointer')
    try:
        next_path.symlink_to('releases/' + name)
        os.replace(next_path, root / 'current')
        sync_dir(root)
    finally:
        next_path.unlink(missing_ok=True)
    # Existing consumers use resolved immutable paths; never terminate their processes.
    return 'updated-next-session'


def tick(root=ROOT, results=RESULTS, force=False):
    with lock(root / 'updater.lock', exclusive=True, nonblocking=True):
        path = root / 'status.json'
        state = read_json(path) if path.exists() else {'schema': 1}
        try:
            current = selected(root).name
            now = time.time()
            if force or now >= state.get('next_check', 0):
                state.update(last_check=now, next_check=now + DAY)
                name = discover(current)
                state.update(available=name, current=current)
                state.pop('error', None)
                if newer(name, current):
                    install(root, name)
                    state.update(pending=name, status='awaiting-validation')
                else:
                    state.pop('pending', None)
                    state['status'] = 'current'
            if state.get('pending'):
                state['status'] = promote(root, results, state['pending'], now)
                if state['status'] == 'updated-next-session':
                    state.pop('pending')
                    state['switched_at'] = time.time()
            state.update(current=selected(root).name, chromium=read_json(selected(root) / 'release.json')['chromium'])
        except Exception as error:
            # Archive and HTTP framing exceptions are not OSError/ValueError.
            # Preserve status/backoff for every ordinary acquisition failure;
            # SystemExit/KeyboardInterrupt still unwind staging and stop the service.
            state.update(status='held-error', error=str(error)[:500])
        atomic_json(path, state)
        print(json.dumps({'browser_update': state}, sort_keys=True), flush=True)
        return state
