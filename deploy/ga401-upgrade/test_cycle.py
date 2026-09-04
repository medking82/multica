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

if __name__ == '__main__': unittest.main()
