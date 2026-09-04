import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import cycle

A, B = 'a' * 40, 'b' * 40

class FakeCycle(cycle.Cycle):
    def __init__(self, path, fail=None):
        super().__init__(cycle.REPO, path)
        self.calls = []; self.fail = fail
    def step(self, name):
        self.calls.append(name)
        if self.fail == name: raise cycle.Failure(name)
    def baseline(self):
        self.step('baseline'); self.head = A
        self.policy = {'upstream_commit': A, 'target_version': '0.4.39'}
    def snapshot(self):
        self.step('snapshot'); return {'source_commit': A}
    def prepare(self, *args): self.step('merge')
    def checks(self): self.step('checks')
    def review(self): self.step('review')
    def release(self): self.step('release'); self.head = B

def release(status='update_available'):
    return {'status': status, 'tag': 'v0.4.40', 'upstream_sha': B, 'current_sha': A}

class Tests(unittest.TestCase):
    def test_no_model_or_remote_deploy_when_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            owner = FakeCycle(Path(d))
            result = owner.execute(lambda _: release('unchanged'))
            self.assertEqual(result['status'], 'unchanged')
            self.assertEqual(owner.calls, ['baseline'])

    def test_success_obeys_gate_order(self):
        with tempfile.TemporaryDirectory() as d:
            owner = FakeCycle(Path(d))
            result = owner.execute(lambda _: release())
            self.assertEqual(owner.calls, ['baseline', 'snapshot', 'merge', 'checks', 'review', 'release'])
            self.assertEqual(result['source_commit'], B)
            self.assertEqual(result['status'], 'complete')

    def test_every_failure_stops_and_future_cycle_does_not_retry(self):
        stages = ['baseline', 'snapshot', 'merge', 'checks', 'review', 'release']
        for stage in stages:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as d:
                root = Path(d); owner = FakeCycle(root, stage)
                with self.assertRaises(cycle.Failure): owner.execute(lambda _: release())
                self.assertEqual(owner.calls, stages[:stages.index(stage)+1])
                self.assertEqual(cycle.read_json(root / 'status.json')['status'], 'needs_attention')
                next_owner = FakeCycle(root)
                with self.assertRaises(cycle.Failure): next_owner.execute(lambda _: release())
                self.assertEqual(next_owner.calls, [])

    def test_interrupted_cycle_is_terminal(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); cycle.write_json(root / 'status.json', {'status': 'running'})
            owner = FakeCycle(root)
            with self.assertRaises(cycle.Failure): owner.execute(lambda _: release())
            self.assertFalse(owner.calls)

    def test_upstream_cannot_change_execution_or_instruction_owners(self):
        for path in ('.sop/workflow.json', 'deploy/ga401-upgrade/upgrade.py',
                     'scripts/custom-desktop/check.mjs', 'AGENTS.md', 'server/CLAUDE.md'):
            self.assertTrue(cycle.protected([path]))
        self.assertFalse(cycle.protected(['server/internal/service/task.go']))

    def test_review_must_be_complete_bound_and_without_unresolved_findings(self):
        report = {'kind': 'claude-review-evidence', 'status': 'completed', 'head': A,
                  'scope': 'staged', 'findings': [], 'attempts': [{'status': 'completed'}],
                  'usage_credits_authorized': False}
        cycle.validate_review(report, A)
        for changes in ({'head': B}, {'status': 'failed'}, {'findings': [{'severity': 'P2'}]},
                        {'attempts': []}, {'usage_credits_authorized': True}, {'scope': 'commit'}):
            with self.assertRaises(cycle.Failure): cycle.validate_review({**report, **changes}, A)

    def test_semver_compares_numerically(self):
        self.assertGreater(cycle.version('v0.4.40'), cycle.version('0.4.9'))
        for bad in ('v0.4.40-rc1', '', None, 'latest'):
            with self.assertRaises(cycle.Failure): cycle.version(bad)

    def test_quota_selection_stdout_is_not_mistaken_for_the_review_result(self):
        selection = {'kind': 'native-review-quota-selection', 'status': 'selected',
                     'selected': {'route': 'antigravity-cli'}}
        result = {'kind': 'claude-review-result', 'packet_sha256': 'a' * 64}
        output = '\n'.join(map(json.dumps, [selection, result]))
        self.assertEqual(cycle.quota_review_result(output), (selection, result))
        for records in ([result], [selection], [result, selection], [selection, result, result],
                        [{**selection, 'status': 'deferred'}, result]):
            with self.assertRaises(cycle.Failure):
                cycle.quota_review_result('\n'.join(map(json.dumps, records)))

    def test_quota_preparation_failure_cannot_start_native_review_or_release(self):
        with tempfile.TemporaryDirectory() as d:
            owner = cycle.Cycle(cycle.REPO, Path(d))
            calls = []
            def command(args, **kwargs):
                calls.append(args)
                if 'risk-classification.py' in args[2]:
                    return json.dumps({'risk': 'high', 'formal_review': 'required'})
                raise cycle.Failure('quota metadata unavailable')
            owner.command = command
            with self.assertRaises(cycle.Failure): owner.review()
            self.assertEqual(len(calls), 2)
            self.assertTrue(calls[1][2].endswith('prepare-quota.py'))
            self.assertIn('--allow-gemini', calls[1])
            self.assertNotIn('--allow-sonnet', calls[1])

    def test_selected_review_binds_single_attempt_and_archived_quota(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); owner = cycle.Cycle(cycle.REPO, root)
            owner.common = root / 'git'; owner.head = A; owner.tree = B
            owner.git = lambda *args: B if args == ('write-tree',) else ''
            packet = 'c' * 64
            chosen = {'route': 'antigravity-cli', 'model': 'gemini-3.8-flash-high'}
            selection = {'kind': 'native-review-quota-selection', 'status': 'selected',
                         'selected': chosen, 'input_sha256': 'd' * 64}
            folder = owner.common / 'sop/claude-reviews' / packet
            folder.mkdir(parents=True)
            report = {'kind': 'claude-review-evidence', 'status': 'completed', 'head': A,
                      'scope': 'staged', 'findings': [], 'usage_credits_authorized': False,
                      'attempts': [{'status': 'completed', 'reviewer': chosen['route'],
                                    'selected_model': chosen['model']}]}
            cycle.write_json(folder / 'report.json', report)
            cycle.write_json(folder / 'quota-selection.json', selection)
            calls = []
            def command(args, **kwargs):
                calls.append(args)
                if args[2].endswith('risk-classification.py'):
                    return json.dumps({'risk': 'high', 'formal_review': 'required'})
                if args[2].endswith('prepare-quota.py'):
                    return json.dumps({'status': 'selected', 'input': args[args.index('--output') + 1]})
                return '\n'.join(map(json.dumps, [selection, {'kind': 'claude-review-result', 'packet_sha256': packet}]))
            owner.command = command
            owner.review()
            self.assertEqual(owner.state['review_packet'], packet)
            self.assertIn('--quota-input', calls[2])
            self.assertNotIn('--review-mode', calls[2])
            self.assertNotIn('--gemini-model', calls[2])
            cycle.write_json(folder / 'quota-selection.json', {**selection, 'input_sha256': 'e' * 64})
            with self.assertRaisesRegex(cycle.Failure, 'binding changed'): owner.review()

if __name__ == '__main__': unittest.main()
