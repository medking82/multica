#!/usr/bin/env python3
"""No network, account HOME, Docker socket or tools write access in this service."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import tempfile
import time

from common import (ROOT, RESULTS, PROVIDERS, DAY, atomic_json, canonical, current_version,
                    load_json, release_path, report_path, require, validation_identity,
                    verify_release, version)


def probe_environment(home):
    # No inherited provider credentials, proxy settings or paid-API fallback.
    return {'HOME': str(home), 'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
            'DISABLE_AUTOUPDATER': '1', 'DISABLE_UPDATES': '1', 'PYTHONUTF8': '1',
            'TMPDIR': str(home), 'NO_COLOR': '1'}


def stop_tree(child):
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait(timeout=10)


def run_probe(argv, home):
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        child = subprocess.Popen([str(x) for x in argv], stdin=subprocess.DEVNULL,
                                 stdout=stdout, stderr=stderr, env=probe_environment(home),
                                 cwd=home, start_new_session=True)
        try:
            code = child.wait(timeout=30)
        finally:
            stop_tree(child)
        require(stdout.tell() <= 1024**2 and stderr.tell() <= 1024**2, 'Probe output exceeds bound')
        stdout.seek(0)
        stderr.seek(0)
        return subprocess.CompletedProcess(argv, code, stdout.read().decode(errors='replace'),
                                           stderr.read().decode(errors='replace'))


def initialize_codex(executable, home):
    with tempfile.TemporaryFile() as stderr:
        child = subprocess.Popen([str(executable), 'app-server'], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=stderr, env=probe_environment(home),
                                 cwd=home, start_new_session=True)
        try:
            child.stdin.write(canonical({'id': 1, 'method': 'initialize', 'params': {
                'clientInfo': {'name': 'ga401_cli_update_check', 'version': '1'},
                'capabilities': {'experimentalApi': True}}}) + b'\n')
            child.stdin.flush()
            deadline, buffer = time.monotonic() + 30, b''
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while time.monotonic() < deadline:
                    for key, _ in selector.select(timeout=min(1, max(0, deadline - time.monotonic()))):
                        block = os.read(key.fileobj.fileno(), 4096)
                        require(block, 'Codex App Server exited before initialize response')
                        buffer += block
                        require(len(buffer) <= 65536, 'Oversized App Server response')
                        while b'\n' in buffer:
                            line, buffer = buffer.split(b'\n', 1)
                            response = json.loads(line)
                            if response.get('id') == 1:
                                require(isinstance(response.get('result'), dict) and not response.get('error'),
                                        'Codex initialize protocol rejected')
                                return
            raise ValueError('Codex App Server initialize timed out')
        finally:
            stop_tree(child)
            child.stdin.close()
            child.stdout.close()


def validate_native(directory, provider, v):
    checks, warnings = [], []
    with tempfile.TemporaryDirectory(prefix='ga401-cli-check-') as temporary:
        home = Path(temporary)
        executable = directory / 'bin' / provider
        result = run_probe([executable, '--version'], home)
        require(result.returncode == 0 and v in result.stdout.split(), 'Native version probe mismatch')
        checks.append('version')
        result = run_probe([executable, '--help'], home)
        require(result.returncode == 0, 'CLI help failed')
        flags = {
            'codex': ('app-server', '--model'),
            'claude': ('--input-format', '--output-format', '--model', '--resume'),
            'agy': ('--model', '--conversation', '--log-file', '--print-timeout'),
        }[provider]
        require(all(flag in result.stdout + result.stderr for flag in flags), 'Multica CLI flags no longer available')
        checks.append('multica-cli-flags')
        if provider == 'codex':
            for path, flag in (('bin/codex-code-mode-host', '--help'), ('codex-path/rg', '--version'),
                               ('codex-resources/bwrap', '--version')):
                require(run_probe([directory / path, flag], home).returncode == 0, path + ' failed')
                checks.append(path)
            zsh = directory / 'codex-resources/zsh/bin/zsh'
            result = run_probe([zsh, '--version'], home)
            if result.returncode:
                # Only reuse the previously reviewed exact 0.151.0 optional ABI exception.
                require(v == '0.151.0', 'New Codex bundled zsh ABI failure; base image review required')
                features = run_probe([executable, 'features', 'list'], home)
                require(features.returncode == 0, 'Cannot establish optional zsh feature state')
                spec = importlib.util.spec_from_file_location('old_bundle_check',
                    Path('/opt/runtime/verify-codex-bundle.py'))
                old = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(old)
                old.optional_zsh_gap(zsh, result, features.stdout)
                warnings.append('0.151.0 optional zsh needs GLIBC_2.38; shell_zsh_fork is false')
            else:
                checks.append('bundled-zsh')
            initialize_codex(executable, home)
            checks.append('app-server-initialize-no-thread-no-inference')
    return checks, warnings


def validate_release(root, results, provider, v):
    directory = release_path(root, provider, v)
    record = verify_release(directory, provider, v)
    report = {'identity': validation_identity(), 'record_sha256': hashlib.sha256(canonical(record)).hexdigest(),
              'provider': provider, 'version': v, 'checked_at': time.time(), 'passed': False}
    try:
        report['checks'], report['warnings'] = validate_native(directory, provider, v)
        require(record == verify_release(directory, provider, v), 'Package changed during validation')
        report['passed'] = True
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        report['error'] = str(error)[:600]
    atomic_json(report_path(results, record), report)
    print(json.dumps(report, sort_keys=True), flush=True)
    return report


def tick(root=ROOT, results=RESULTS, force=False):
    reports = []
    for provider in PROVIDERS:
        current = current_version(root, provider)
        releases = root / provider / 'releases'
        candidates = [p.name for p in releases.iterdir() if p.is_dir() and not p.is_symlink()]
        # Old binaries remain on disk for rollback, but aren't endlessly re-probed.
        selected = {current, max(candidates, key=version)}
        for v in sorted(selected, key=version):
            # The record is small; validate_release re-hashes the full package
            # when due. Promotion independently re-hashes before every switch.
            record = load_json(release_path(root, provider, v) / 'release.json')
            require(record.get('schema') == 1 and record.get('provider') == provider
                    and record.get('version') == v, 'Release identity mismatch')
            report_file = report_path(results, record)
            previous = load_json(report_file) if report_file.exists() else {}
            checked = previous.get('checked_at', 0)
            if not force and isinstance(checked, (int, float)) and 0 <= time.time() - checked < DAY / 2:
                continue
            reports.append(validate_release(root, results, provider, v))
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('watch', 'once'))
    args = parser.parse_args()
    require(os.getuid() == 1002, 'Validator must run as isolated UID 1002')
    require(not os.access(ROOT, os.W_OK) and os.access(RESULTS, os.W_OK), 'Unsafe validator volume access')
    require(set(Path('/sys/class/net').iterdir()) == {Path('/sys/class/net/lo')}, 'Validator must have no network')
    while True:
        try:
            reports = tick(force=args.command == 'once')
            if args.command == 'once':
                raise SystemExit(any(not x['passed'] for x in reports))
        except (OSError, ValueError, KeyError, TypeError) as error:
            print(json.dumps({'validation_error': str(error)[:600]}), flush=True)
            if args.command == 'once':
                raise SystemExit(1)
        time.sleep(60)


if __name__ == '__main__':
    main()
