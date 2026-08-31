#!/usr/bin/env python3
"""Daily official CLI downloads; promotion requires an independent offline report."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from common import (ROOT, RESULTS, PROVIDERS, MAX_BYTES, MAX_FILES, DAY, approved_report,
                    atomic_json, current_version, load_json, lock, make_record,
                    release_path, report_path, require, safe_relative, sync_dir, verify_release, version)

NPM = 'https://registry.npmjs.org/@openai/codex'
CLAUDE = 'https://downloads.claude.ai/claude-code-releases'
AGY = 'https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/linux_amd64.json'
CLAUDE_KEY = '31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE'
MAX_DOWNLOAD = 768 * 1024**2


def approved_url(url):
    parsed = urlsplit(url)
    require(parsed.scheme == 'https' and not parsed.username and not parsed.password
            and parsed.port in (None, 443) and not parsed.query and not parsed.fragment,
            'Only allowlisted public HTTPS release URLs are accepted')
    paths = {
        'registry.npmjs.org': r'/@openai/codex/(latest|[0-9.]+-linux-x64|-/codex-[0-9.]+-linux-x64\.tgz)',
        'downloads.claude.ai': r'/claude-code-releases/(stable|[0-9.]+/(manifest\.json(\.sig)?|linux-x64/claude))',
        'antigravity-cli-auto-updater-974169037036.us-central1.run.app': r'/manifests/linux_amd64\.json',
        'storage.googleapis.com': r'/antigravity-public/antigravity-cli/[0-9.]+-[0-9]+/linux-x64/cli_linux_x64\.tar\.gz',
    }
    require(parsed.hostname in paths and re.fullmatch(paths[parsed.hostname], parsed.path),
            'Release URL is outside the reviewed publisher paths')
    return url


class ReleaseRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        approved_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, destination=None, limit=65536):
    """Bound both bytes and wall time; revalidate every redirect before connecting."""
    approved_url(url)
    opener = build_opener(ReleaseRedirects())
    started, count, chunks = time.monotonic(), 0, []
    with opener.open(Request(url, headers={'User-Agent': 'GA401-CLI-Updater/1'}), timeout=30) as response:
        approved_url(response.geturl())
        with destination.open('xb') if destination else tempfile.TemporaryFile() as stream:
            while True:
                require(time.monotonic() - started < 600, 'Release download deadline exceeded')
                block = response.read(min(1024 * 1024, limit + 1 - count))
                if not block:
                    break
                count += len(block)
                require(count <= limit, 'Release download exceeds size limit')
                stream.write(block)
                if destination is None:
                    chunks.append(block)
            stream.flush()
            if destination is not None:
                os.fsync(stream.fileno())
    return b''.join(chunks)


def json_url(url):
    return json.loads(fetch(url))


def discover(provider):
    if provider == 'codex':
        latest = json_url(NPM + '/latest')
        v = latest['version']
        version(v)
        require(latest.get('name') == '@openai/codex' and
                latest.get('optionalDependencies', {}).get('@openai/codex-linux-x64') ==
                'npm:@openai/codex@' + v + '-linux-x64', 'Unsupported Codex platform dependency')
        package = json_url(NPM + '/' + v + '-linux-x64')
        require(package.get('name') == '@openai/codex' and package.get('version') == v + '-linux-x64',
                'Codex platform metadata mismatch')
        integrity = package['dist']['integrity']
        require(isinstance(integrity, str) and integrity.startswith('sha512-'), 'Codex needs SHA512 integrity')
        digest = base64.b64decode(integrity[7:], validate=True)
        require(len(digest) == 64, 'Malformed npm integrity')
        return {'version': v, 'url': approved_url(package['dist']['tarball']),
                'algorithm': 'sha512', 'checksum': digest.hex()}
    if provider == 'claude':
        v = fetch(CLAUDE + '/stable').decode().strip()
        version(v)
        return {'version': v, 'url': CLAUDE + '/' + v + '/linux-x64/claude'}
    require(provider == 'agy', 'Unknown provider')
    data = json_url(AGY)
    version(data['version'])
    require(re.fullmatch('[a-f0-9]{128}', data['sha512']), 'Malformed Antigravity checksum')
    url = approved_url(data['url'])
    require('/' + data['version'] + '-' in urlsplit(url).path, 'Antigravity manifest version mismatch')
    return {'version': data['version'], 'url': url, 'algorithm': 'sha512', 'checksum': data['sha512']}


def claude_manifest(v, temporary):
    manifest = temporary / 'manifest.json'
    signature = temporary / 'manifest.json.sig'
    fetch(CLAUDE + '/' + v + '/manifest.json', manifest)
    fetch(CLAUDE + '/' + v + '/manifest.json.sig', signature)
    # The checked-in public key is public release material, never an account key.
    armor = Path(__file__).with_name('claude-release.asc').read_text().splitlines()
    encoded = ''.join(line for line in armor if line and not line.startswith(('-', '=')))
    keyring = temporary / 'claude-release.gpg'
    keyring.write_bytes(base64.b64decode(encoded, validate=True))
    result = subprocess.run(['gpgv', '--homedir', str(temporary), '--status-fd', '1',
                             '--keyring', str(keyring), str(signature), str(manifest)],
                            capture_output=True, text=True, timeout=30, check=False)
    require(result.returncode == 0 and '[GNUPG:] VALIDSIG ' + CLAUDE_KEY + ' ' in result.stdout,
            'Claude release manifest signature is invalid or from an untrusted key')
    data = load_json(manifest)
    require(data.get('version') == v, 'Claude manifest version mismatch')
    platform = data['platforms']['linux-x64']
    require(platform.get('binary') == 'claude' and re.fullmatch('[a-f0-9]{64}', platform['checksum'])
            and 0 < platform['size'] <= MAX_DOWNLOAD, 'Unsupported Claude manifest')
    return platform


def extract_payload(payload, destination, provider):
    """Extract regular files ourselves; never honor tar ownership, links or devices."""
    prefix = 'package/vendor/x86_64-unknown-linux-musl/'
    seen, count, size = set(), 0, 0
    with tarfile.open(payload, 'r:gz') as archive:
        for entry in archive:
            count += 1
            require(count <= MAX_FILES + 32, 'Too many archive entries')
            name = entry.name.removeprefix('./').rstrip('/')
            safe_relative(name)
            require(name not in seen, 'Duplicate archive path')
            seen.add(name)
            require(entry.isdir() or entry.isfile(), 'Archive links/devices are forbidden')
            if entry.isdir():
                continue
            size += entry.size
            require(0 <= entry.size <= MAX_DOWNLOAD and size <= MAX_BYTES, 'Expanded archive exceeds bounds')
            if provider == 'codex':
                if name in ('package/README.md', 'package/package.json'):
                    continue
                require(name.startswith(prefix), 'Unexpected Codex archive layout')
                relative = name[len(prefix):]
                require(relative != 'release.json', 'Publisher cannot provide installer control data')
            else:
                require(provider == 'agy' and name == 'antigravity', 'Unexpected Antigravity archive layout')
                relative = 'bin/agy'
            safe_relative(relative)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(entry) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            target.chmod(0o555 if entry.mode & 0o111 else 0o444)


def install(root, provider, source):
    v = source['version']
    destination = release_path(root, provider, v)
    if destination.exists():
        return verify_release(destination, provider, v)
    require(shutil.disk_usage(root).free >= 4 * 1024**3, 'Less than 4 GiB free; old versions retained, update held')
    # Only this freshly-created staging directory is ever cleaned automatically.
    with tempfile.TemporaryDirectory(prefix='.download-', dir=root) as name:
        temporary = Path(name)
        bundle = temporary / 'bundle'
        bundle.mkdir()
        provenance = dict(source)
        if provider == 'claude':
            platform = claude_manifest(v, temporary)
            provenance.update(algorithm='sha256', checksum=platform['checksum'], signing_key=CLAUDE_KEY)
        payload = temporary / 'payload'
        fetch(source['url'], payload, limit=MAX_DOWNLOAD)
        digest = hashlib.new(provenance['algorithm'])
        with payload.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        require(digest.hexdigest() == provenance['checksum'], 'Downloaded release checksum mismatch')
        if provider == 'claude':
            require(payload.stat().st_size == platform['size'], 'Claude binary size mismatch')
            (bundle / 'bin').mkdir()
            os.replace(payload, bundle / 'bin/claude')
            (bundle / 'bin/claude').chmod(0o555)
        else:
            extract_payload(payload, bundle, provider)
        record = make_record(bundle, provider, v, provenance)
        os.rename(bundle, destination)  # Existing versions are never replaced.
        sync_dir(destination.parent)
        return record


def promote(root, results, provider, v, now=None):
    current = current_version(root, provider)
    require(version(v) > version(current), 'Automatic downgrade/reinstall is forbidden')
    directory = release_path(root, provider, v)
    # Cheap report lookup first; don't scan hundreds of MB on every idle retry.
    # A full checksum/inventory verification still runs under the exclusive lease.
    record = load_json(directory / 'release.json')
    if not approved_report(results, record, now):
        path = report_path(results, record)
        if path.exists() and load_json(path).get('passed') is False:
            raise ValueError('Offline validation failed: ' + str(load_json(path).get('error', 'see validator log'))[:500])
        return 'awaiting-validation'
    try:
        with lock(root / provider / 'in-use.lock', exclusive=True, nonblocking=True):
            # Close the race with a launcher starting between the idle check and switch.
            require(current_version(root, provider) == current, 'Current version changed unexpectedly')
            require(record == verify_release(directory, provider, v), 'Release record changed before promotion')
            require(approved_report(results, record, now), 'Validation report changed before promotion')
            link = root / provider / 'current.next'
            require(not link.exists() and not link.is_symlink(), 'Unexpected pending pointer')
            try:
                link.symlink_to('releases/' + v)
                os.replace(link, root / provider / 'current')
                sync_dir(root / provider)
            finally:
                link.unlink(missing_ok=True)
        return 'updated'
    except BlockingIOError:
        return 'waiting-for-idle'


def tick(root=ROOT, results=RESULTS, force=False):
    with lock(root / 'updater.lock', exclusive=True, nonblocking=True):
        status_path = root / 'status.json'
        state = load_json(status_path) if status_path.exists() else {'schema': 1, 'providers': {}}
        require(state.get('schema') == 1 and isinstance(state.get('providers'), dict), 'Invalid updater status')
        for provider in PROVIDERS:
            now = time.time()
            item = state['providers'].setdefault(provider, {})
            try:
                current = current_version(root, provider)
                if force or now >= item.get('next_check', 0):
                    # A failure is recorded and retried next day, not hammered every minute.
                    item.update(last_check=now, next_check=now + DAY)
                    source = discover(provider)
                    item.update(available=source['version'], current=current)
                    item.pop('error', None)
                    if version(source['version']) > version(current):
                        install(root, provider, source)
                        item.update(pending=source['version'], status='awaiting-validation')
                    else:
                        item.pop('pending', None)
                        item['status'] = 'current' if source['version'] == current else 'holding-newer-version'
                if item.get('pending'):
                    item['status'] = promote(root, results, provider, item['pending'], now)
                    if item['status'] == 'updated':
                        item.update(current=item.pop('pending'), switched_at=time.time())
                item['current'] = current_version(root, provider)
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
                item.update(status='held-error', error=str(error)[:600])
            atomic_json(status_path, state)
        print(json.dumps(state, sort_keys=True), flush=True)
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('watch', 'once', 'status'))
    args = parser.parse_args()
    if args.command == 'status':
        print(json.dumps(load_json(ROOT / 'status.json'), indent=2))
        return
    require(os.getuid() == 1001, 'Updater must run as isolated UID 1001')

    def stop(signum, _frame):
        # Unwind the active TemporaryDirectory/lock on normal container shutdown.
        # SIGKILL/power-loss leftovers remain an explicit manual-cleanup case.
        raise SystemExit(128 + signum)

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, stop)
    while True:
        try:
            state = tick(force=args.command == 'once')
            if args.command == 'once':
                raise SystemExit(any(x.get('status') == 'held-error' for x in state['providers'].values()))
        except BlockingIOError:
            if args.command == 'once':
                raise SystemExit('Another update cycle is already running')
        if args.command == 'once':
            return
        time.sleep(60)


if __name__ == '__main__':
    main()
