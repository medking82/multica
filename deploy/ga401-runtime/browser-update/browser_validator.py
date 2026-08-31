"""Run each complete browser generation in an account-free, offline consumer image."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

from browser_common import (ROOT, RESULTS, BASES, DAY, atomic_json, canonical,
    package_environment, parse_generation, read_json, report_path, require, selected,
    validation_identity, verify_release, version)


def probe(directory, consumer):
    with tempfile.TemporaryDirectory(prefix='browser-check-') as temporary:
        home = Path(temporary)
        command = ['node', '/opt/runtime/browser-update/browser_probe.cjs', str(directory)]
        if consumer == 'login':
            command = ['xvfb-run', '-a', '-s', '-screen 0 1440x900x24 -nolisten tcp', *command, '--headed']
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            child = subprocess.Popen(command, cwd=home, env=package_environment(directory, home),
                stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, start_new_session=True)
            try:
                result = child.wait(timeout=90)
            finally:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait(timeout=10)
            require(stdout.tell() < 1024**2 and stderr.tell() < 1024**2, 'Browser probe output exceeds bound')
            stdout.seek(0)
            stderr.seek(0)
            require(result == 0, 'Browser smoke failed: ' + stderr.read(2000).decode(errors='replace'))
            data = json.loads(stdout.read())
            require(data.get('passed') is True and data.get('generation') == directory.name,
                    'Invalid browser smoke result')
            return data['checks']


def validate(directory, results, consumer):
    record = verify_release(directory)
    report = {'consumer': consumer, 'identity': validation_identity(consumer),
        'record_sha256': hashlib.sha256(canonical(record)).hexdigest(),
        'generation': record['generation'], 'checked_at': time.time(), 'passed': False}
    try:
        report['checks'] = probe(directory, consumer)
        require(record == verify_release(directory), 'Browser package changed during validation')
        report['passed'] = True
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        report['error'] = str(error)[:1000]
    atomic_json(report_path(results, record, consumer), report)
    print(json.dumps({'browser_validation': report}, sort_keys=True), flush=True)
    return report


def tick(consumer, root=ROOT, results=RESULTS, force=False):
    require(consumer in BASES, 'Unknown browser validation consumer')
    current = selected(root)
    candidates = [p for p in (root / 'releases').iterdir() if p.is_dir() and not p.is_symlink()]
    newest = max(candidates, key=lambda p: tuple(version(v) for v in parse_generation(p.name)))
    reports = []
    for directory in sorted({current, newest}):
        record = read_json(directory / 'release.json')
        previous_path = report_path(results, record, consumer)
        previous = read_json(previous_path) if previous_path.exists() else {}
        checked = previous.get('checked_at', 0)
        if not force and isinstance(checked, (int, float)) and 0 <= time.time() - checked < DAY / 2:
            continue
        reports.append(validate(directory, results, consumer))
    return reports
