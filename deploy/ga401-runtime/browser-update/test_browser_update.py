import base64
import copy
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import shutil
import stat
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

import browser_common as contract
import browser_launcher as launcher
import browser_updater as updater
import browser_watch as watch
import profile_guard


class PureContractTests(unittest.TestCase):
    def test_nested_npm_paths_fit_browser_inventory_without_allowing_escape(self):
        name = ('node_modules/@playwright/mcp/node_modules/playwright/'
                'node_modules/playwright-core/lib/server/injected/recorder/ui/overlay.js')
        self.assertEqual(contract.safe_relative(name).as_posix(), name)
        for bad in ['../escape', '/absolute', 'a/../b', 'a//b', 'a/./b', 'a\\b',
                    'C:/escape', '.']:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                contract.safe_relative(bad)
        with self.assertRaises(ValueError):
            contract.safe_relative('/'.join(['a'] * 33))

    def test_generation_and_component_downgrade(self):
        old = contract.generation('1.62.1', '0.0.79')
        self.assertTrue(contract.newer(contract.generation('1.62.2', '0.0.79'), old))
        self.assertTrue(contract.newer(contract.generation('1.62.1', '0.0.80'), old))
        self.assertFalse(contract.newer(contract.generation('1.63.0', '0.0.78'), old))
        self.assertFalse(contract.newer(old, old))
        for value in ['../current', 'pw-1.62.1-alpha-mcp-0.0.79', 'pw-01.62.1-mcp-0.0.79']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                contract.parse_generation(value)

    def test_publisher_urls_are_bounded_before_redirect(self):
        for url in ['https://registry.npmjs.org/playwright/latest',
                    'https://registry.npmjs.org/@playwright/mcp/-/mcp-0.0.79.tgz',
                    'https://cdn.playwright.dev/builds/cft/151.0.7922.34/linux64/chrome-linux64.zip',
                    'https://storage.googleapis.com/chrome-for-testing-public/151.0.7922.34/linux64/chrome-linux64.zip']:
            self.assertEqual(updater.approved_url(url), url)
        for url in ['http://registry.npmjs.org/playwright/latest',
                    'https://user:secret@registry.npmjs.org/playwright/latest',
                    'https://registry.npmjs.org:1234/playwright/latest',
                    'https://registry.npmjs.org/unrelated/latest',
                    'https://registry.npmjs.org/playwright/latest?token=private',
                    'https://cdn.playwright.dev/../../account',
                    'https://storage.googleapis.com/another-bucket/file.zip',
                    'https://127.0.0.1/playwright/latest']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                updater.approved_url(url)

    def package(self):
        return {'name': 'playwright', 'version': '1.62.1',
                'dependencies': {'playwright-core': '1.62.1'},
                'dist': {'integrity': 'sha512-' + base64.b64encode(b'x' * 64).decode(),
                         'tarball': 'https://registry.npmjs.org/playwright/-/playwright-1.62.1.tgz'}}

    def test_registry_sri_and_exact_dependencies_required(self):
        good = self.package()
        with patch.object(updater, 'fetch', return_value=json.dumps(good).encode()):
            self.assertEqual(updater.package_metadata('playwright', 'latest'), good)
        cases = []
        for key, value in [('name', 'other'), ('version', '1.63.0-alpha'),
                           ('dependencies', {'evil': '1.0.0'}),
                           ('dependencies', {'playwright-core': '^1.62.1'}),
                           ('optionalDependencies', {'unexpected': '1.0.0'})]:
            case = copy.deepcopy(good)
            case[key] = value
            cases.append(case)
        case = copy.deepcopy(good)
        case['dist']['integrity'] = 'sha1-deadbeef'
        cases.append(case)
        for case in cases:
            with self.subTest(case=case), patch.object(updater, 'fetch', return_value=json.dumps(case).encode()), self.assertRaises(ValueError):
                updater.package_metadata('playwright', 'latest')

    def test_discovery_never_downgrades_one_component(self):
        with patch.object(updater, 'package_metadata', side_effect=[{'version': '1.61.0'}, {'version': '0.0.80'}]):
            self.assertEqual(updater.discover('pw-1.62.1-mcp-0.0.79'), 'pw-1.62.1-mcp-0.0.80')

    def test_fetch_checks_declared_length_but_accepts_complete_chunked_response(self):
        url = 'https://registry.npmjs.org/playwright/latest'
        payload = b'fixture'
        for length, succeeds in [('7', True), (None, True), ('10', False),
                                 ('invalid', False), ('9999999999', False)]:
            with self.subTest(length=length), patch.object(updater, 'build_opener') as opener:
                response = opener.return_value.open.return_value.__enter__.return_value
                response.headers = {} if length is None else {'Content-Length': length}
                response.geturl.return_value = url
                response.read.side_effect = io.BytesIO(payload).read
                if succeeds:
                    self.assertEqual(updater.fetch(url), payload)
                else:
                    with self.assertRaisesRegex(ValueError, 'length|truncated'):
                        updater.fetch(url)

    def test_native_dry_run_is_exactly_three_paired_artifacts(self):
        root = Path('/approved/release')
        info = {'revision': '1234', 'chromium': '151.0.7922.34', 'ffmpeg_revision': '1011'}
        core = contract.core_path(root) / '.local-browsers'
        text = '\n\n'.join(f'Browser\n  Install location: {core / name}\n  Download url: {url}' for name, url in [
            ('chromium-1234', 'https://cdn.playwright.dev/builds/cft/151.0.7922.34/linux64/chrome-linux64.zip'),
            ('ffmpeg-1011', 'https://cdn.playwright.dev/dbazure/download/playwright/builds/ffmpeg/1011/ffmpeg-linux.zip'),
            ('chromium_headless_shell-1234', 'https://cdn.playwright.dev/builds/cft/151.0.7922.34/linux64/chrome-headless-shell-linux64.zip')])
        self.assertEqual(len(updater.download_plan(text, root, info)), 3)
        for bad in [text.replace(str(core), '/outside'), text.replace('151.0.7922.34', '152.0.1.1'), text.split('\n\n')[0], text + '\n\n' + text]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                updater.download_plan(bad, root, info)

    def test_clean_download_validation_environment(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'not-a-real-key', 'HTTP_PROXY': 'untrusted', 'NODE_OPTIONS': '--require=other'}):
            env = contract.package_environment(Path('/bundle'), Path('/temporary'))
        self.assertNotIn('OPENAI_API_KEY', env)
        self.assertNotIn('HTTP_PROXY', env)
        self.assertNotIn('NODE_OPTIONS', env)
        self.assertEqual(env['PLAYWRIGHT_BROWSERS_PATH'], '0')

    def test_reports_bind_both_consumer_bases_and_expire(self):
        record = {'generation': 'pw-1.62.1-mcp-0.0.79'}
        with tempfile.TemporaryDirectory() as temporary:
            results = Path(temporary)
            self.assertNotEqual(contract.validation_identity('runtime'), contract.validation_identity('login'))
            self.assertFalse(contract.approved(results, record, 'runtime', 100))
            for consumer in contract.BASES:
                report = {'consumer': consumer, 'identity': contract.validation_identity(consumer),
                    'record_sha256': hashlib.sha256(contract.canonical(record)).hexdigest(),
                    'checked_at': 100, 'passed': True}
                path = contract.report_path(results, record, consumer)
                contract.atomic_json(path, report)
                self.assertTrue(contract.approved(results, record, consumer, 100))
                self.assertFalse(contract.approved(results, record, consumer, 99))
                self.assertFalse(contract.approved(results, record, consumer, 100 + contract.DAY + 1))
                report['record_sha256'] = 'wrong'
                contract.atomic_json(path, report)
                self.assertFalse(contract.approved(results, record, consumer, 100))

    def test_launcher_pins_module_and_browser_to_same_generation(self):
        directory = Path('/opt/browser-tools/releases/pw-1.62.1-mcp-0.0.79')
        record = {'executable': 'node_modules/playwright/node_modules/playwright-core/.local-browsers/chromium-1234/chrome-linux64/chrome'}
        with patch.object(launcher, 'read_json', return_value=record), patch.object(launcher, 'check_layout'):
            args, env = launcher.launch_spec('mcp', [], directory)
        self.assertIn(str(directory / record['executable']), args)
        self.assertEqual(args[1], str(directory / 'node_modules/@playwright/mcp/cli.js'))
        self.assertNotIn('/current/', env['NODE_PATH'])
        self.assertIn('--isolated', args)
        self.assertIn('--headless', args)

    def test_provider_failure_does_not_skip_browser_cycle(self):
        import updater as provider_updater
        with (patch.object(provider_updater, 'tick', side_effect=ValueError('fixture')),
              patch.object(updater, 'tick', return_value={'status': 'current'}) as browser_tick):
            self.assertTrue(watch.cycle('updater'))
            browser_tick.assert_called_once()


class ArchiveTests(unittest.TestCase):
    def test_tar_rejects_links_escape_and_duplicates(self):
        for names, link in [(['package/../escape'], False), (['package/link'], True),
                            (['package/a', 'package/a'], False)]:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                payload = root / 'package.tgz'
                with tarfile.open(payload, 'w:gz') as archive:
                    for name in names:
                        item = tarfile.TarInfo(name)
                        if link:
                            item.type = tarfile.SYMTYPE
                            item.linkname = '/outside'
                            archive.addfile(item)
                        else:
                            item.size = 1
                            archive.addfile(item, io.BytesIO(b'x'))
                with self.assertRaises(ValueError):
                    updater.extract_tar(payload, root / 'bundle')

    def test_zip_rejects_links_and_escape(self):
        for name, mode in [('../escape', stat.S_IFREG | 0o644), ('link', stat.S_IFLNK | 0o777)]:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                payload = root / 'browser.zip'
                with zipfile.ZipFile(payload, 'w') as archive:
                    item = zipfile.ZipInfo(name)
                    item.external_attr = mode << 16
                    archive.writestr(item, b'/outside')
                with self.assertRaises(ValueError):
                    updater.extract_zip(payload, root / 'bundle')

    @unittest.skipUnless(os.name == 'posix', 'Linux executable/setuid modes')
    def test_zip_strips_setuid_and_preserves_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / 'browser.zip'
            with zipfile.ZipFile(payload, 'w') as archive:
                item = zipfile.ZipInfo('chrome')
                item.external_attr = (stat.S_IFREG | 0o4755) << 16
                archive.writestr(item, b'fixture')
            updater.extract_zip(payload, root / 'bundle')
            self.assertEqual((root / 'bundle/chrome').stat().st_mode & 0o7777, 0o555)


@unittest.skipUnless(os.name == 'posix', 'Linux symlink, owner/mode and immutable-generation contract')
class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'updater.lock').touch(mode=0o444)
        self.results = self.root / 'results'
        self.results.mkdir()
        self.old = self.fixture('1.62.1', '151.0.7922.34')
        self.new = self.fixture('1.62.2', '151.0.7922.35')
        (self.root / 'current').symlink_to('releases/' + self.old.name)

    def fixture(self, pw, chrome):
        name = contract.generation(pw, '0.0.79')
        directory = self.root / 'releases' / name
        for rel, package, v in [('playwright', 'playwright', pw),
                              ('playwright/node_modules/playwright-core', 'playwright-core', pw),
                              ('@playwright/mcp', '@playwright/mcp', '0.0.79')]:
            folder = directory / 'node_modules' / rel
            folder.mkdir(parents=True)
            (folder / 'package.json').write_text(json.dumps({'name': package, 'version': v}))
            (folder / 'cli.js').write_text('// fixture')
        browsers = {'browsers': [{'name': 'chromium', 'revision': '1234', 'browserVersion': chrome},
            {'name': 'chromium-headless-shell', 'revision': '1234', 'browserVersion': chrome},
            {'name': 'ffmpeg', 'revision': '1011'}]}
        (contract.core_path(directory) / 'browsers.json').write_text(json.dumps(browsers))
        exe = contract.core_path(directory) / '.local-browsers/chromium-1234/chrome-linux64/chrome'
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b'fake browser; never execute')
        exe.chmod(0o555)
        contract.write_record(directory, name, exe.relative_to(directory).as_posix(), {'test': True})
        return directory

    def reports(self, directory, consumers=('runtime', 'login')):
        record = contract.read_json(directory / 'release.json')
        for consumer in consumers:
            contract.atomic_json(contract.report_path(self.results, record, consumer), {
                'consumer': consumer, 'identity': contract.validation_identity(consumer),
                'record_sha256': hashlib.sha256(contract.canonical(record)).hexdigest(),
                'checked_at': time.time(), 'passed': True})

    def test_both_validators_required_and_old_generation_retained(self):
        old_pin = contract.selected(self.root)
        self.reports(self.new, ('runtime',))
        self.assertEqual(updater.promote(self.root, self.results, self.new.name), 'awaiting-login-validation')
        self.assertEqual(contract.selected(self.root), self.old)
        self.reports(self.new)
        self.assertEqual(updater.promote(self.root, self.results, self.new.name), 'updated-next-session')
        self.assertEqual(contract.selected(self.root), self.new)
        self.assertEqual(contract.verify_release(old_pin)['playwright'], '1.62.1')

    def test_changed_binary_after_reports_cannot_promote(self):
        self.reports(self.new)
        (self.new / 'node_modules/playwright/cli.js').write_text('// modified')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            updater.promote(self.root, self.results, self.new.name)
        self.assertEqual(contract.selected(self.root), self.old)

    def test_changed_record_invalidates_reports(self):
        self.reports(self.new)
        path = self.new / 'release.json'
        data = contract.read_json(path)
        data['provenance']['modified'] = True
        contract.atomic_json(path, data)
        self.assertEqual(updater.promote(self.root, self.results, self.new.name), 'awaiting-runtime-validation')

    def test_browser_downgrade_blocked_even_if_package_versions_increase(self):
        directory = self.fixture('1.63.0', '150.0.1.1')
        self.reports(directory)
        with self.assertRaisesRegex(ValueError, 'downgrade'):
            updater.promote(self.root, self.results, directory.name)

    def test_symlink_inside_release_refused(self):
        (self.new / 'external').symlink_to('/etc/passwd')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            contract.verify_release(self.new)

    def test_linked_current_outside_root_refused(self):
        (self.root / 'current').unlink()
        (self.root / 'current').symlink_to('../outside')
        with self.assertRaises(ValueError):
            contract.selected(self.root)

    def test_failed_download_is_recorded_without_changing_current(self):
        with patch.object(updater, 'discover', side_effect=ValueError('fixture failure')):
            state = updater.tick(self.root, self.results, force=True)
        self.assertEqual(state['status'], 'held-error')
        self.assertEqual(contract.selected(self.root), self.old)
        self.assertGreater(state['next_check'], time.time() + 86000)

    def test_corrupt_archive_or_http_failure_records_daily_hold_not_minute_redownload(self):
        for failure in (zipfile.BadZipFile('broken fixture'), tarfile.ReadError('broken fixture'),
                        http.client.IncompleteRead(b'fixture', 100)):
            contract.atomic_json(self.root / 'status.json', {'schema': 1, 'next_check': 0})
            with (self.subTest(failure=type(failure).__name__),
                  patch.object(updater, 'discover', return_value=self.new.name) as discover,
                  patch.object(updater, 'install', side_effect=failure) as install):
                first = updater.tick(self.root, self.results)
                self.assertEqual(first['status'], 'held-error')
                self.assertGreater(first['next_check'], time.time() + 86000)
                self.assertEqual(contract.read_json(self.root / 'status.json'), first)
                next_minute = time.time() + 60
                with patch.object(updater.time, 'time', return_value=next_minute):
                    second = updater.tick(self.root, self.results)
                # A cheap current-version metadata refresh may add `chromium`;
                # the persisted error/backoff and all earlier fields stay intact.
                self.assertEqual({key: second[key] for key in first}, first)
                self.assertEqual(contract.selected(self.root), self.old)
                discover.assert_called_once()
                install.assert_called_once()


@unittest.skipUnless(os.name == 'posix', 'Linux private profile owner/mode/lease contract')
class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.home.chmod(0o700)
        self.profile = self.home / 'chromium-profile'
        self.profile.mkdir(mode=0o700)
        (self.profile / 'Last Version').write_text('151.0.7922.34')
        (self.profile / 'Cookies').write_bytes(b'synthetic account fixture, not a real cookie')
        self.record = {'chromium': '151.0.7922.34', 'generation': 'pw-1.62.1-mcp-0.0.79'}

    def prepare(self, record=None):
        with patch.object(shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(100 * 1024**3, 0, 100 * 1024**3)):
            fd = profile_guard.prepare_profile(self.home, record or self.record)
        os.close(fd)

    def test_private_backup_preserves_profile_and_skips_only_singletons(self):
        (self.profile / 'SingletonLock').symlink_to('GA401-old-123')
        self.prepare()
        marker = json.loads((self.home / 'browser-update-attempt.json').read_text())
        backup = self.home / marker['backup']
        self.assertEqual((backup / 'Cookies').read_bytes(), (self.profile / 'Cookies').read_bytes())
        self.assertFalse((backup / 'SingletonLock').is_symlink())
        self.assertTrue((self.profile / 'SingletonLock').is_symlink())
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.home / 'browser-update-attempt.json').stat().st_mode & 0o777, 0o600)
        self.prepare()
        self.assertEqual(len(list((self.home / 'browser-update-backups').iterdir())), 1)
        self.prepare({**self.record, 'chromium': '151.0.7922.35'})
        self.assertEqual(len(list((self.home / 'browser-update-backups').iterdir())), 2)

    def test_profile_downgrade_refused(self):
        self.prepare()
        before = (self.home / 'browser-update-attempt.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'downgrade'):
            self.prepare({**self.record, 'chromium': '150.0.1.1'})
        self.assertEqual((self.home / 'browser-update-attempt.json').read_bytes(), before)

    def test_disk_shortage_preserves_original_and_does_not_mark_attempt(self):
        with patch.object(shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(1000, 1000, 0)):
            with self.assertRaisesRegex(ValueError, 'space'):
                profile_guard.prepare_profile(self.home, self.record)
        self.assertTrue((self.profile / 'Cookies').is_file())
        self.assertFalse((self.home / 'browser-update-attempt.json').exists())

    def test_external_profile_links_refused(self):
        (self.profile / 'linked-cookie').symlink_to('/etc/passwd')
        with self.assertRaisesRegex(ValueError, 'linked'):
            self.prepare()

    def test_linked_migration_marker_refused(self):
        (self.home / 'browser-update-attempt.json').symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):
            self.prepare()

    def test_shared_home_permissions_refused(self):
        self.home.chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'private'):
            self.prepare()

    def test_concurrent_browser_start_refused(self):
        with patch.object(shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(100 * 1024**3, 0, 100 * 1024**3)):
            fd = profile_guard.prepare_profile(self.home, self.record)
            try:
                with self.assertRaises(BlockingIOError):
                    profile_guard.prepare_profile(self.home, self.record)
            finally:
                os.close(fd)


if __name__ == '__main__':
    unittest.main()
