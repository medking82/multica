import hashlib, json, tarfile, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import upgrade

COMMIT = "a" * 40

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.base = Path(self.tmp.name) / "upgrades"
        self.root = self.base / COMMIT; (self.root / "source/server/migrations").mkdir(parents=True)
        (self.root / "source/Dockerfile").write_text("FROM scratch\n")
        (self.root / "source/Dockerfile.web").write_text("FROM scratch\n")
        (self.root / "source/server/migrations/450.up.sql").write_text("-- migration\n")
        with tarfile.open(self.root / "source.tar", 'w:') as archive:
            for path in sorted((self.root / 'source').rglob('*')):
                if path.is_file(): archive.add(path, arcname=path.relative_to(self.root).as_posix())
        self.old = upgrade.BASE
        upgrade.BASE = self.base
        prior = self.base / ('b' * 40); prior.mkdir()
        images = {k: 'sha256:' + '1' * 64 for k in ('backend', 'frontend', 'runtime')}
        (prior / 'state.json').write_text(json.dumps({'commit': 'b' * 40, 'status': 'complete',
            'completed': list(upgrade.PHASES), 'images': images, 'rehearsal': {'ledger': '450_fixture'}}))
        policy = self.root / 'source/deploy/ga401-upgrade'; policy.mkdir(parents=True)
        transition = {'prior_commit': 'b' * 40, 'prior_ledger': '450_fixture', 'prior_images': images,
            'target_version': '0.5.2-custom.109', 'upstream_commit': 'c' * 40, 'source_commit': COMMIT,
            'source_tree': 'd' * 40,
            'source_archive_sha256': hashlib.sha256((self.root / 'source.tar').read_bytes()).hexdigest(),
            'deployment_tool_commit': 'e' * 40,
            'deployment_tool_sha256': upgrade.file_hash(Path(upgrade.__file__))}
        (self.root / 'transition.json').write_text(json.dumps(transition))

    def tearDown(self):
        upgrade.BASE = self.old; self.tmp.cleanup()

    def make(self): return upgrade.Upgrade(self.root, COMMIT)

    def test_health_probe_declares_its_client_identity(self):
        with patch.object(upgrade.urllib.request, 'urlopen') as opener:
            opener.return_value.__enter__.return_value.status = 200
            upgrade.http('https://agent.hankee.com/health')
            self.assertEqual(opener.call_args.args[0].get_header('User-agent'), 'MulticaGA401HealthCheck/1.0')

    def test_runtime_base_uses_retained_tag_even_after_compose_used_an_image_id(self):
        tag = 'multica-ga401-runtime:0.4.39-ga401.fixture'
        with patch.object(upgrade, 'inspect', side_effect=[{'RepoTags': [tag]}, {'Id': upgrade.OLD['runtime']}]) as inspected:
            self.assertEqual(upgrade.runtime_base_tag(), tag)
            self.assertEqual(inspected.call_args_list[0].args, (upgrade.OLD['runtime'], 'image'))
        with patch.object(upgrade, 'inspect', side_effect=[{'RepoTags': [tag]}, {'Id': 'wrong-image'}]):
            with self.assertRaisesRegex(upgrade.Failure, 'tag drift'):
                upgrade.runtime_base_tag()

    def test_exact_root_and_commit_are_required(self):
        with self.assertRaisesRegex(upgrade.Failure, "exact source"):
            upgrade.Upgrade(self.root, "bad")
        with self.assertRaisesRegex(upgrade.Failure, "unexpected release root"):
            upgrade.Upgrade(self.base / "other", COMMIT)

    def test_transition_is_external_and_binds_source_archive_and_runner(self):
        u = self.make()
        self.assertEqual(u.transition['source_commit'], COMMIT)
        self.assertEqual(u.state['deployment_tool_commit'], 'e' * 40)
        (self.root / 'source.tar').write_bytes(b'changed archive')
        with self.assertRaisesRegex(upgrade.Failure, 'source archive digest mismatch'):
            self.make()
        with self.assertRaisesRegex(upgrade.Failure, 'external task metadata'):
            upgrade.Upgrade(self.root, COMMIT, self.root / 'source/deploy/ga401-upgrade/transition.json')

    def test_phase_receipt_refuses_transition_drift_without_rewriting_state(self):
        u = self.make()
        with patch.object(u, 'preflight'):
            u.phase('preflight')
        state_before = (self.root / 'state.json').read_bytes()
        transition_path = self.root / 'transition.json'
        transition = json.loads(transition_path.read_text())
        transition['target_version'] = '0.5.3-custom.109'
        transition_path.write_text(json.dumps(transition))
        with self.assertRaisesRegex(upgrade.Failure, 'release input binding drift: transition_sha256'):
            self.make()
        self.assertEqual((self.root / 'state.json').read_bytes(), state_before)

    def test_candidate_builds_only_app_images_and_retains_runtime_image(self):
        u = self.make()
        calls = []
        def docker(*args, **kwargs):
            calls.append(args)
            if args[0] == 'run': return (u.version + ' ' + COMMIT[:8]).encode()
            return b''
        def inspect(value, kind='container'):
            if kind == 'image': return {'Id': 'sha256:' + ('2' if 'backend' in value else '3') * 64}
            raise AssertionError((value, kind))
        with patch.object(u, 'originals'), patch.object(upgrade, 'docker', side_effect=docker), \
             patch.object(upgrade, 'inspect', side_effect=inspect):
            u.build()
        self.assertEqual(set(u.tags), {'backend', 'frontend'})
        self.assertEqual(u.state['images']['runtime'], upgrade.OLD['runtime'])
        builds = [call for call in calls if call[0] == 'build']
        self.assertEqual(len(builds), 2)
        self.assertFalse(any('runtime-build' in str(call) for call in calls))

    def test_migration_reconciliation_allows_only_documented_category_mapping(self):
        before = {'schema_history': ['001', '450'], 'candidate_migrations': ['001', '450', '451', '544'],
                  'entity_ids': 'same-ids', 'content_hashes': {'issue': 'i', 'comment': 'c', 'project': 'p', 'skill': 's'},
                  'task_ids': 'same-tasks', 'task_statuses': 'completed:true:1',
                  'eligible_legacy_fallback_tasks': '0', 'agent_statuses': 'idle:1',
                  'eligible_autopilot_triggers': '0|empty', 'comment_agent_delivery_exists': False,
                  'comment_agent_delivery_rows': None,
                  'pr_links': {'issue_pull_request': '0|2|linkhash', 'issue_vcs_pull_request': '0|3|vcs-hash'},
                  'issue_status_categories': 'backlog:1,blocked:1,cancelled:1,done:1,in_progress:1,in_review:1,todo:1',
                  'new_tables': {'maintenance_job': None, 'channel_reply_delivery': None, 'issue_wakeup': None,
                                 'issue_wakeup_receipt': None, 'task_supplement_capability': None,
                                 'task_supplement': None, 'instance_telemetry_state': None}}
        after = dict(before)
        after.update(schema_history=['001', '450', '451', '544'],
                     issue_status_categories='closed:1,done:1,started:3,unstarted:2',
                     pr_links={'issue_pull_request': '2|linkhash', 'issue_vcs_pull_request': '3|vcs-hash'},
                     new_tables={'maintenance_job': 0, 'channel_reply_delivery': 0, 'issue_wakeup': 0,
                                 'issue_wakeup_receipt': 0, 'task_supplement_capability': 0,
                                 'task_supplement': 0, 'instance_telemetry_state': 1})
        upgrade.validate_migration_snapshots(before, after)
        after['entity_ids'] = 'changed'
        with self.assertRaisesRegex(upgrade.Failure, 'durable entity IDs'):
            upgrade.validate_migration_snapshots(before, after)

    def test_upload_backup_is_readonly_and_restored_only_to_tmpfs(self):
        u = self.make()
        calls = []
        def docker(*args, **kwargs):
            calls.append(args)
            if len(calls) == 1:
                kwargs['output'].write(b'fixture archive')
                return b''
            return b'2\n8\t/restore\n'
        with patch.object(upgrade, 'docker', side_effect=docker):
            receipt = u.snapshot_uploads()
        self.assertEqual(receipt['restored_files'], 2)
        self.assertEqual(receipt['restored_kib'], 8)
        self.assertIn('type=volume,src=multica_backend_uploads,dst=/data,readonly', calls[0])
        self.assertIn('--network', calls[0]); self.assertIn('none', calls[0])
        self.assertIn('--tmpfs', calls[1]); self.assertIn('/restore:rw,size=8g', calls[1])
        self.assertTrue(any('dst=/backup/backend-uploads.tar,readonly' in part for part in calls[1]))
        self.assertFalse(any(part == '-p' or part == '--publish' for call in calls for part in call))

    def test_phase_order_duplicate_and_failure_are_terminal(self):
        u = self.make()
        with patch.object(u, "preflight"):
            u.phase("preflight")
        with self.assertRaisesRegex(upgrade.Failure, "phase order"):
            u.phase("preflight")
        (self.root / "state.json").unlink()
        u = self.make()
        with patch.object(u, "preflight", side_effect=upgrade.Failure("fixture failure")):
            with self.assertRaises(upgrade.Failure): u.phase("preflight")
        state = json.loads((self.root / "state.json").read_text())
        self.assertEqual(state["status"], "FAILED_NEEDS_DECISION")
        with self.assertRaisesRegex(upgrade.Failure, "previous failure"):
            u.phase("preflight")

    def test_interrupted_phase_requires_operator_decision(self):
        state = self.make().state
        state.update({"commit": COMMIT, "completed": [], "running": "build"})
        (self.root / "state.json").write_text(json.dumps(state))
        u = self.make()
        with self.assertRaisesRegex(upgrade.Failure, "interrupted"):
            u.phase("preflight")

    def test_idle_accepts_terminal_tasks_and_rejects_unknown_or_provider(self):
        def sql(query, **kwargs):
            if "status NOT IN" in query: return "0"
            if "agent_task_queue" in query: return "completed|2\nfailed|1\ncancelled|1"
            return "0"
        with patch.object(upgrade, "sql", side_effect=sql), patch.object(upgrade, "docker", return_value=b"sleep\n"):
            upgrade.idle()
        with patch.object(upgrade, "sql", return_value="queued|1"):
            with self.assertRaisesRegex(upgrade.Failure, "active or unknown"):
                upgrade.idle()
        with patch.object(upgrade, "sql", return_value="0"), patch.object(upgrade, "docker", return_value=b"gemini\n"):
            with self.assertRaisesRegex(upgrade.Failure, "provider"):
                upgrade.idle()

    def test_rehearsal_rejects_published_or_production_mount(self):
        for observation in (
            {'HostConfig': {'PortBindings': {'5432/tcp': [{}]}}, 'Mounts': []},
            {'HostConfig': {'PortBindings': {}}, 'Mounts': [{'Type': 'volume', 'Name': 'multica_pgdata'}]},
        ):
            u = self.make()
            u.state['containers'] = {'postgres': {'image': 'pg17-image-id'}}
            with patch.object(u, 'originals'), patch.object(u, 'dump', return_value=self.root / 'fixture.dump'), \
                 patch.object(u, 'snapshot_uploads', return_value={'sha256': 'f'*64, 'bytes': 1,
                                                                   'restored_files': 0, 'restored_kib': 0}), \
                 patch.object(upgrade, 'docker') as commands, patch.object(upgrade, 'inspect', return_value=observation):
                with self.assertRaisesRegex(upgrade.Failure, 'persistent mount or published port'):
                    u.rehearse()
                self.assertFalse(any('pg_restore' in call.args for call in commands.call_args_list))

    def test_final_dump_failure_occurs_after_stop_and_prevents_promotion(self):
        u = self.make()
        u.state['images'] = dict(upgrade.OLD)
        events = []
        def compose_config(*args):
            return {'services': {r: {'image': 'original'} for r in ('backend', 'frontend', 'runtime')}}
        def compose(runtime, *args):
            events.append(('compose', args))
            return json.dumps(compose_config()).encode()
        def dump(*args):
            events.append(('dump', args))
            raise upgrade.Failure('final backup failed')
        with patch.object(u, 'originals'), patch.object(upgrade, 'idle'), \
             patch.object(upgrade, 'ledger', return_value=upgrade.OLD_LEDGER), \
             patch.object(upgrade, 'inspect', side_effect=lambda tag, kind: {'Id': u.state['images'][next(r for r,t in u.tags.items() if t == tag)]}), \
             patch.object(upgrade, 'compose_config', side_effect=compose_config), \
             patch.object(u, 'compose', side_effect=compose), \
             patch.object(upgrade, 'docker', side_effect=lambda *args, **kw: events.append(('docker', args)) or b''), \
             patch.object(upgrade, 'sql', return_value='0'), \
             patch.object(upgrade, 'identities', return_value='fixture-ids'), \
             patch.object(u, 'dump', side_effect=dump), \
             patch.object(upgrade, 'APP', self.root / 'missing-app'), patch.object(upgrade, 'RUNTIME', self.root / 'missing-runtime'):
            with self.assertRaisesRegex(upgrade.Failure, 'final backup failed'):
                u.activate()
        stop_index = next(i for i,e in enumerate(events) if e[0] == 'docker' and e[1][0] == 'stop')
        dump_index = next(i for i,e in enumerate(events) if e[0] == 'dump')
        self.assertLess(stop_index, dump_index)
        self.assertEqual(events[stop_index][1], ('stop', 'multica-frontend-1', 'multica-backend-1'))
        self.assertFalse(any(e[0] == 'compose' and e[1][0] == 'runtime' for e in events))
        self.assertTrue(u.state['writers_stopped'])
        self.assertFalse(any(e[0] == 'compose' and 'up' in e[1] for e in events))

    def test_build_refuses_source_drift_and_uses_pinned_candidate(self):
        u = self.make()
        with patch.object(u, "originals", side_effect=upgrade.Failure("compose or environment changed")):
            with self.assertRaisesRegex(upgrade.Failure, "compose"):
                u.build()

    def test_phase_failure_persists_without_retry_or_cleanup(self):
        u = self.make()
        with patch.object(u, "preflight", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError): u.phase("preflight")
        state = json.loads((self.root / "state.json").read_text())
        self.assertEqual(state["failure"]["message"], "boom")
        self.assertFalse((self.root / "rollback").exists())

if __name__ == "__main__": unittest.main()
