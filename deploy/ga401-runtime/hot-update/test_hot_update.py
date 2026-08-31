import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

import common
import launcher
import updater
import validator
import verify_policy as policy

DIRECTORY = Path(__file__).parent
spec = importlib.util.spec_from_file_location('original_policy_tests', DIRECTORY.parent / 'test_policy.py')
original = importlib.util.module_from_spec(spec)
spec.loader.exec_module(original)


def configuration():
    config = original.valid_config()
    runtime = config['services']['runtime']
    runtime.update(image=policy.IMAGE, restart='unless-stopped', stop_grace_period=120)
    for name in ('runtime', 'updater', 'validator'):
        service = runtime if name == 'runtime' else copy.deepcopy(runtime)
        service['volumes'] = [{'type': 'volume', 'source': v, 'target': p, 'read_only': ro}
                              for v, p, ro in policy.MOUNTS[name]]
        if name != 'runtime':
            uid, cpus, pids = policy.SIDECARS[name]
            service.update(user=f'{uid}:{uid}', cpus=cpus, pids_limit=pids,
                           mem_limit=1024**3, memswap_limit=1024**3,
                           entrypoint=['python3', f'/opt/runtime/hot-update/{name}.py'], command=['watch'])
            service.pop('environment')
            service['networks'] = {'cli-updates': None}
            if name == 'validator':
                service.pop('networks')
                service['network_mode'] = 'none'
        config['services'][name] = service
    config['volumes'] = {v: {'name': policy.PROJECT + '_' + v} for v in ('runtime-home', 'cli-tools', 'cli-validation')}
    config['networks']['cli-updates'] = {'name': policy.PROJECT + '_cli-updates'}
    return config


def inspection():
    items = []
    for name in policy.MOUNTS:
        item = original.valid_inspect()[0]
        item['Image'] = policy.IMAGE_ID
        item['Config']['Image'] = policy.IMAGE
        item['Config']['Labels']['com.docker.compose.service'] = name
        item['HostConfig']['RestartPolicy'] = {'Name': 'unless-stopped'}
        item['HostConfig']['Binds'] = [policy.PROJECT + '_' + v + ':' + p + (':ro' if ro else ':rw')
                                       for v, p, ro in policy.MOUNTS[name]]
        item['Mounts'] = [{'Type': 'volume', 'Name': policy.PROJECT + '_' + v,
                          'Destination': p, 'RW': not ro} for v, p, ro in policy.MOUNTS[name]]
        if name != 'runtime':
            uid, cpus, pids = policy.SIDECARS[name]
            item['Config'].update(User=f'{uid}:{uid}', Entrypoint=['python3', f'/opt/runtime/hot-update/{name}.py'], Cmd=['watch'])
            network = 'none' if name == 'validator' else policy.PROJECT + '_cli-updates'
            item['HostConfig'].update(Memory=1024**3, MemorySwap=1024**3, NanoCpus=cpus * 10**9,
                                      PidsLimit=pids, NetworkMode=network)
            item['NetworkSettings']['Networks'] = {network: {}}
        items.append(item)
    return items


def package(root, v='1.0.0', provider='claude', body=None):
    directory = root / provider / 'releases' / v
    (directory / 'bin').mkdir(parents=True)
    cli = directory / 'bin' / provider
    cli.write_text(body or '#!/bin/sh\nprintf "%s\\n" "' + v + '"\n')
    cli.chmod(0o755)
    if os.name == 'posix':
        record = common.make_record(directory, provider, v, {'test': True})
    else:
        with patch.object(common, 'check_layout'):
            record = common.make_record(directory, provider, v, {'test': True})
    return record


def approve(results, record, checked_at=None):
    common.atomic_json(common.report_path(results, record), {
        'identity': common.validation_identity(), 'record_sha256': hashlib.sha256(common.canonical(record)).hexdigest(),
        'checked_at': time.time() if checked_at is None else checked_at, 'passed': True})


def ready_line(child):
    with selectors.DefaultSelector() as selector:
        selector.register(child.stdout, selectors.EVENT_READ)
        if not selector.select(timeout=10):
            raise AssertionError('Launcher did not become ready within 10 seconds')
        line = child.stdout.readline()
        if not line:
            child.wait(timeout=10)
            raise AssertionError('Launcher exited before ready: ' + child.stderr.read())
        return line


class ContractTests(unittest.TestCase):
    def test_version_rejects_prerelease_paths_and_downgrade_order(self):
        self.assertGreater(common.version('0.151.0'), common.version('0.99.9'))
        for value in ('../1.0.0', '1.2.3-beta', '01.2.3', '1.2', '1.2.3\n', '', None, '1' * 50 + '.0.0'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                common.version(value)

    def test_publisher_url_allowlist_and_redirects(self):
        for url in (updater.NPM + '/latest', updater.CLAUDE + '/stable', updater.AGY,
                    'https://registry.npmjs.org/@openai/codex/-/codex-0.151.0-linux-x64.tgz'):
            self.assertEqual(updater.approved_url(url), url)
        for url in ('http://registry.npmjs.org/@openai/codex/latest', 'https://127.0.0.1/x',
                    'https://downloads.claude.ai.evil.test/claude-code-releases/stable',
                    updater.AGY + '?token=private', updater.AGY.replace('https://', 'https://user:pass@'),
                    'https://storage.googleapis.com/another-bucket/binary',
                    'https://registry.npmjs.org:444/@openai/codex/latest'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                updater.approved_url(url)
        with self.assertRaises(ValueError):
            updater.ReleaseRedirects().redirect_request(None, None, 302, '', {}, 'http://169.254.169.254/')

    def test_safe_paths(self):
        self.assertEqual(str(common.safe_relative('bin/claude')), 'bin/claude')
        for name in ('/tmp/bad', '../escape', 'bin/../../escape', 'a//b', 'a/./b', 'C:/bad', 'a\\b', 'a\x00b'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                common.safe_relative(name)

    def test_extract_refuses_links_duplicates_and_traversal(self):
        for kind in ('symlink', 'hardlink', 'parent', 'absolute', 'duplicate', 'unexpected', 'device'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                payload, output = root / 'archive.tgz', root / 'output'
                output.mkdir()
                with tarfile.open(payload, 'w:gz') as archive:
                    entry = tarfile.TarInfo({'parent': '../escape', 'absolute': '/tmp/escape',
                                           'unexpected': 'install.sh'}.get(kind, 'antigravity'))
                    entry.mode = 0o755
                    if kind in ('symlink', 'hardlink'):
                        entry.type = tarfile.SYMTYPE if kind == 'symlink' else tarfile.LNKTYPE
                        entry.linkname = '../escape'
                    elif kind == 'device':
                        entry.type = tarfile.CHRTYPE
                    archive.addfile(entry)
                    if kind == 'duplicate':
                        archive.addfile(entry)
                with self.assertRaises(ValueError):
                    updater.extract_payload(payload, output, 'agy')
                self.assertFalse((root / 'escape').exists())

    def test_extract_regular_binary_and_size_cap(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / 'output'
            output.mkdir()
            payload = root / 'archive.tgz'
            with tarfile.open(payload, 'w:gz') as archive:
                entry = tarfile.TarInfo('antigravity')
                entry.mode, entry.size = 0o755, 4
                archive.addfile(entry, io.BytesIO(b'test'))
            updater.extract_payload(payload, output, 'agy')
            self.assertEqual((output / 'bin/agy').read_bytes(), b'test')
            with patch.object(updater, 'MAX_BYTES', 3), self.assertRaises(ValueError):
                updater.extract_payload(payload, root / 'other', 'agy')

    def test_checksum_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package(root)
            (root / 'claude/releases/1.0.0/bin/claude').write_text('tampered')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                common.verify_release(root / 'claude/releases/1.0.0', 'claude', '1.0.0')

    def test_download_checksum_failure_never_installs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'agy/releases').mkdir(parents=True)

            def bad_payload(_url, destination, **_kwargs):
                destination.write_bytes(b'corrupted release')

            source = {'version': '1.1.22', 'url': updater.AGY,
                      'algorithm': 'sha512', 'checksum': '0' * 128}
            roomy_disk = updater.shutil.disk_usage(root)._replace(free=8 * 1024**3)
            with patch.object(updater, 'fetch', side_effect=bad_payload), \
                    patch.object(updater.shutil, 'disk_usage', return_value=roomy_disk), \
                    self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                updater.install(root, 'agy', source)
            self.assertEqual(list((root / 'agy/releases').iterdir()), [])
            self.assertEqual(list(root.glob('.download-*')), [])

    def test_claude_signature_failure_never_accepts_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            def fake_fetch(_url, destination, **_kwargs):
                destination.write_bytes(b'unsigned data')

            result = subprocess.CompletedProcess([], 1, '', 'bad signature')
            with patch.object(updater, 'fetch', side_effect=fake_fetch), \
                    patch.object(updater.subprocess, 'run', return_value=result), \
                    self.assertRaisesRegex(ValueError, 'signature is invalid'):
                updater.claude_manifest('2.1.251', Path(temporary))

    def test_report_requires_exact_release_checker_and_freshness(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = package(root)
            self.assertFalse(common.approved_report(root, record))
            approve(root, record)
            self.assertTrue(common.approved_report(root, record))
            self.assertFalse(common.approved_report(root, {**record, 'version': '1.0.1'}))
            approve(root, record, time.time() - common.DAY - 1)
            self.assertFalse(common.approved_report(root, record))
            approve(root, record, time.time() + 60)
            self.assertFalse(common.approved_report(root, record))
            report = common.load_json(common.report_path(root, record))
            report.update(checked_at=time.time(), identity='wrong')
            common.atomic_json(common.report_path(root, record), report)
            self.assertFalse(common.approved_report(root, record))

    def test_probe_environment_has_no_auth_or_paid_fallback(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test-only', 'GH_TOKEN': 'test-only'}):
            env = validator.probe_environment(Path('/tmp/check'))
            self.assertNotIn('OPENAI_API_KEY', env)
            self.assertNotIn('GH_TOKEN', env)
            self.assertNotIn('HTTP_PROXY', env)
            self.assertEqual(env['HOME'], '/tmp/check' if os.name == 'posix' else '\\tmp\\check')
            self.assertEqual(env['DISABLE_UPDATES'], '1')


class IsolationTests(unittest.TestCase):
    def test_config_and_inspect_valid(self):
        policy.check_config(configuration())
        policy.check_inspect(inspection())

    def test_compose_duration_representation_keeps_two_minute_grace(self):
        for value in ('120s', '2m', '2m0s'):
            config = configuration()
            config['services']['runtime']['stop_grace_period'] = value
            policy.check_config(config)
        config['services']['runtime']['stop_grace_period'] = '2s'
        with self.assertRaisesRegex(ValueError, 'drain grace'):
            policy.check_config(config)

    def test_rejects_rw_runtime_tools_or_updater_results(self):
        for name, target in (('runtime', '/opt/agent-tools'), ('updater', '/opt/validation-results')):
            config = configuration()
            next(x for x in config['services'][name]['volumes'] if x['target'] == target)['read_only'] = False
            with self.assertRaises(ValueError):
                policy.check_config(config)

    def test_rejects_validator_network_or_login_home(self):
        for name in ('updater', 'validator'):
            config = configuration()
            config['services'][name]['volumes'].append({'type': 'volume', 'source': 'runtime-home', 'target': '/home/agent'})
            with self.assertRaises(ValueError):
                policy.check_config(config)
        config = configuration()
        config['services']['validator']['network_mode'] = 'host'
        with self.assertRaises(ValueError):
            policy.check_config(config)

    def test_rejects_socket_privilege_api_keys_and_foreign_image(self):
        for role in policy.MOUNTS:
            for key, value in (('privileged', True), ('cap_add', ['SYS_ADMIN']),
                               ('ports', ['9222:9222']), ('image', 'foreign:latest'),
                               ('environment', {'OPENAI_API_KEY': 'test-only'})):
                with self.subTest(role=role, key=key), self.assertRaises(ValueError):
                    config = configuration()
                    config['services'][role][key] = value
                    policy.check_config(config)
        for i in range(3):
            with self.subTest(index=i), self.assertRaises(ValueError):
                items = inspection()
                items[i]['Mounts'].append({'Type': 'bind', 'Source': '/var/run/docker.sock', 'Destination': '/var/run/docker.sock', 'RW': True})
                policy.check_inspect(items)

    def test_inspect_rejects_live_image_network_and_rw_drift(self):
        for index, change in ((0, 'image'), (1, 'mount'), (2, 'network')):
            items = inspection()
            if change == 'image':
                items[index]['Image'] = 'sha256:' + '0' * 64
            elif change == 'mount':
                items[index]['Mounts'][1]['RW'] = True
            else:
                items[index]['HostConfig']['NetworkMode'] = 'host'
            with self.subTest(change=change), self.assertRaises(ValueError):
                policy.check_inspect(items)


@unittest.skipUnless(os.name == 'posix', 'Linux flock/process lifecycle integration')
class LinuxLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.old = package(self.root)
        self.new = package(self.root, '1.0.1')
        (self.root / 'claude/current').symlink_to('releases/1.0.0')
        (self.root / 'claude/in-use.lock').touch()
        approve(self.root, self.new)

    def test_shared_lock_defers_atomic_promotion(self):
        with common.lock(self.root / 'claude/in-use.lock'):
            self.assertEqual(updater.promote(self.root, self.root, 'claude', '1.0.1'), 'waiting-for-idle')
            self.assertEqual(common.current_version(self.root, 'claude'), '1.0.0')
        self.assertEqual(updater.promote(self.root, self.root, 'claude', '1.0.1'), 'updated')
        self.assertEqual(common.current_version(self.root, 'claude'), '1.0.1')
        self.assertTrue((self.root / 'claude/releases/1.0.0/bin/claude').exists())
        with self.assertRaises(ValueError):
            updater.promote(self.root, self.root, 'claude', '1.0.0')

    def test_busy_promotion_does_not_hash_the_payload(self):
        with common.lock(self.root / 'claude/in-use.lock'), \
                patch.object(updater, 'verify_release', side_effect=AssertionError('unnecessary payload scan')):
            self.assertEqual(updater.promote(self.root, self.root, 'claude', '1.0.1'), 'waiting-for-idle')

    def test_idle_promotion_still_rejects_payload_tampering(self):
        (self.root / 'claude/releases/1.0.1/bin/claude').write_text('corrupt')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            updater.promote(self.root, self.root, 'claude', '1.0.1')
        self.assertEqual(common.current_version(self.root, 'claude'), '1.0.0')

    def test_fresh_validator_report_does_not_scan_payloads(self):
        approve(self.root, self.old)
        with patch.object(validator, 'PROVIDERS', ('claude',)), \
                patch.object(validator, 'verify_release', side_effect=AssertionError('unnecessary payload scan')):
            self.assertEqual(validator.tick(self.root, self.root), [])

    def test_expired_validator_report_requires_fresh_payload_checks(self):
        approve(self.root, self.old)
        approve(self.root, self.new, time.time() - common.DAY)
        (self.root / 'claude/releases/1.0.1/bin/claude').write_text('corrupt')
        with patch.object(validator, 'PROVIDERS', ('claude',)), self.assertRaisesRegex(ValueError, 'checksum'):
            validator.tick(self.root, self.root)

    def test_failed_and_missing_validation_cannot_switch(self):
        path = common.report_path(self.root, self.new)
        path.unlink()
        self.assertEqual(updater.promote(self.root, self.root, 'claude', '1.0.1'), 'awaiting-validation')
        common.atomic_json(path, {'passed': False, 'error': 'ABI test'})
        with self.assertRaisesRegex(ValueError, 'ABI test'):
            updater.promote(self.root, self.root, 'claude', '1.0.1')
        self.assertEqual(common.current_version(self.root, 'claude'), '1.0.0')

    def test_foreign_current_and_release_symlinks_fail(self):
        link = self.root / 'claude/current'
        link.unlink()
        link.symlink_to('/tmp/foreign')
        with self.assertRaises(ValueError):
            common.current_version(self.root, 'claude')
        (self.root / 'claude/releases/2.0.0').symlink_to('/tmp')
        with self.assertRaises(ValueError):
            common.release_path(self.root, 'claude', '2.0.0')

    def test_real_launcher_holds_lock_preserves_stdio_and_next_version(self):
        script = ('#!/usr/bin/python3\nimport json,os,sys\n'
                  'print(json.dumps(["ready",os.getpgrp(),sys.argv[1:]]),flush=True)\n'
                  'print(sys.stdin.readline().strip(),flush=True)\nsys.exit(7)\n')
        cli = self.root / 'claude/releases/1.0.0/bin/claude'
        cli.write_text(script)
        code = 'from pathlib import Path;import launcher;raise SystemExit(launcher.launch(Path(__import__("sys").argv[1]),"claude",__import__("sys").argv[2:]))'
        child = subprocess.Popen([sys.executable, '-c', code, str(self.root), 'space argument', '--literal=$x'],
            cwd=DIRECTORY, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True)
        self.addCleanup(lambda: child.poll() is None and validator.stop_tree(child))
        ready = json.loads(ready_line(child))
        self.assertEqual(ready[1], child.pid)
        self.assertEqual(ready[2], ['space argument', '--literal=$x'])
        self.assertEqual(updater.promote(self.root, self.root, 'claude', '1.0.1'), 'waiting-for-idle')
        output, error = child.communicate('input stays literal\n', timeout=10)
        self.assertEqual((child.returncode, output.strip(), error), (7, 'input stays literal', ''))
        self.assertEqual(updater.promote(self.root, self.root, 'claude', '1.0.1'), 'updated')
        result = subprocess.run([sys.executable, '-c', code, str(self.root), '--version'], cwd=DIRECTORY,
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.stdout.strip(), '1.0.1')
        self.assertEqual(result.returncode, 0)

    def test_launcher_forwards_term_and_releases_lease(self):
        script = ('#!/usr/bin/python3\nimport signal,sys,time\n'
                  'signal.signal(signal.SIGTERM,lambda *_:sys.exit(23))\n'
                  'print("ready",flush=True)\ntime.sleep(20)\n')
        (self.root / 'claude/releases/1.0.0/bin/claude').write_text(script)
        code = 'from pathlib import Path;import launcher;raise SystemExit(launcher.launch(Path(__import__("sys").argv[1]),"claude",[]))'
        child = subprocess.Popen([sys.executable, '-c', code, str(self.root)], cwd=DIRECTORY,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        self.addCleanup(lambda: child.poll() is None and validator.stop_tree(child))
        self.assertEqual(ready_line(child).strip(), 'ready')
        child.send_signal(signal.SIGTERM)
        child.communicate(timeout=10)
        self.assertEqual(child.returncode, 23)
        self.assertEqual(updater.promote(self.root, self.root, 'claude', '1.0.1'), 'updated')

    def test_validator_failure_report_is_not_approval(self):
        with patch.object(validator, 'validate_native', side_effect=ValueError('required flag missing')):
            report = validator.validate_release(self.root, self.root, 'claude', '1.0.1')
        self.assertFalse(report['passed'])
        self.assertFalse(common.approved_report(self.root, self.new))

    def test_updater_sigterm_cleans_only_its_active_download(self):
        (self.root / 'agy/releases').mkdir(parents=True)
        # Test the actual main() signal handling and install() context unwinding,
        # without network, inference or changing the production volume.
        code = '''from pathlib import Path
import sys,time
from types import SimpleNamespace
import updater
root=Path(sys.argv[1])
updater.os.getuid=lambda:1001
updater.shutil.disk_usage=lambda _:SimpleNamespace(free=8*1024**3)
def fetch(url,destination,**kwargs):
 destination.write_bytes(b'partial download')
 print('ready',flush=True)
 time.sleep(30)
updater.fetch=fetch
source={'version':'1.1.22','url':updater.AGY,'algorithm':'sha512','checksum':'0'*128}
updater.tick=lambda **kwargs:updater.install(root,'agy',source)
sys.argv=['updater','once']
updater.main()
'''
        child = subprocess.Popen([sys.executable, '-c', code, str(self.root)], cwd=DIRECTORY,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        self.addCleanup(lambda: child.poll() is None and validator.stop_tree(child))
        self.assertEqual(ready_line(child).strip(), 'ready')
        self.assertEqual(len(list(self.root.glob('.download-*'))), 1)
        child.send_signal(signal.SIGTERM)
        child.communicate(timeout=10)
        self.assertEqual(child.returncode, 128 + signal.SIGTERM)
        self.assertEqual(list(self.root.glob('.download-*')), [])
        self.assertTrue((self.root / 'claude/releases/1.0.0').is_dir())
        self.assertTrue((self.root / 'claude/releases/1.0.1').is_dir())


if __name__ == '__main__':
    unittest.main()
