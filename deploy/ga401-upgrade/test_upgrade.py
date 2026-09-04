import json, tempfile, unittest
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
        self.old = upgrade.BASE
        upgrade.BASE = self.base

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
        (self.root / "state.json").write_text(json.dumps({"commit": COMMIT, "completed": [], "running": "build"}))
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
