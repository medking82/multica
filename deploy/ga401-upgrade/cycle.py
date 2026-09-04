"""Six-hour Windows owner. No model call when upstream is unchanged.

One cycle may merge, check, review and start one canonical SOP release. Any
failure/interruption is durable and requires an operator; this has no resume.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import uuid

from discover import discover

REPO = Path(r'C:\github\multica-ga401-upgrade-0439')
BRANCH = 'codex/ga401-upgrade-0439'
REMOTE = 'https://github.com/multica-ai/multica.git'
SKILLS = Path(r'C:\Users\Marck\.agents\skills')
SHA = re.compile(r'^[0-9a-f]{40}$')
PROTECTED = ('.sop/', 'deploy/ga401-upgrade/', 'scripts/custom-desktop/')
TERMINAL = {'needs_attention', 'running'}

class Failure(RuntimeError): pass

def require(ok, message):
    if not ok: raise Failure(message)

def now(): return datetime.now(timezone.utc).isoformat()

def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)

def read_json(path): return json.loads(path.read_text(encoding='utf-8-sig'))

def protected(paths):
    return any(p.startswith(PROTECTED) or Path(p).name in ('AGENTS.md', 'CLAUDE.md') for p in paths)

def version(value):
    require(isinstance(value, str) and re.fullmatch(r'v?\d+\.\d+\.\d+', value), 'invalid stable version')
    return tuple(map(int, value.removeprefix('v').split('.')))

def validate_review(report, head):
    require(report.get('status') == 'completed' and report.get('head') == head
            and report.get('scope') == 'staged' and report.get('kind') == 'claude-review-evidence',
            'native review incomplete or source binding changed')
    require(report.get('findings') == [], 'review findings require operator adjudication')
    require(report.get('attempts') and report.get('usage_credits_authorized') is False,
            'native review evidence missing or unexpected credits')


def quota_review_result(output):
    records = [json.loads(line) for line in output.splitlines() if line.strip()]
    require(len(records) == 2 and records[0].get('kind') == 'native-review-quota-selection'
            and records[0].get('status') == 'selected' and records[0].get('selected')
            and records[1].get('kind') == 'claude-review-result',
            'expected quota selection followed by one native review result')
    return records[0], records[1]

@contextmanager
def lock(path):
    import msvcrt
    with path.open('a+b') as handle:
        if handle.tell() == 0: handle.write(b'0'); handle.flush()
        handle.seek(0)
        try: msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError: raise Failure('another automatic update cycle is active')
        try: yield
        finally:
            handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

class Cycle:
    def __init__(self, repo, state_root):
        require(repo.resolve() == REPO, 'unexpected automation checkout')
        self.repo, self.root = repo, state_root
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'status.json'
        self.state = read_json(self.path) if self.path.exists() else {'status': 'ready'}

    def command(self, args, *, data=None, timeout=3600):
        # Native Review owns a full hour per selected attempt, including quota fallback.
        p = subprocess.run(args, cwd=self.repo, input=data, text=True, encoding='utf-8',
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        with (self.root / 'commands.log').open('a', encoding='utf-8') as log:
            log.write(now() + ' ' + Path(args[0]).name + ' exit=' + str(p.returncode) + '\n')
            log.write(p.stdout + p.stderr + '\n')
        require(p.returncode == 0, f'{Path(args[0]).name} failed; see commands.log')
        return p.stdout.strip()

    def git(self, *args): return self.command(['git', *args])
    def sop(self, *args): return json.loads(self.command([sys.executable, '-B', '.sop/sop.py', '--repo', '.', *args]))
    def save(self, status, **values):
        self.state.update(status=status, checked_at=now(), **values)
        write_json(self.path, self.state)

    def baseline(self):
        require(not self.git('status', '--porcelain'), 'automation checkout has user changes')
        require(self.git('branch', '--show-current') == BRANCH, 'automation branch changed')
        origin = self.git('remote', 'get-url', 'origin')
        require(origin.removesuffix('.git') in ('https://github.com/medking82/multica', 'git@github.com:medking82/multica'), 'origin changed')
        self.head = self.git('rev-parse', 'HEAD')
        require(SHA.fullmatch(self.head), 'invalid local source')
        tip = self.git('ls-remote', 'origin', 'refs/heads/' + BRANCH).split()
        require(tip and tip[0] == self.head, 'remote branch drift')
        self.common = (self.repo / self.git('rev-parse', '--git-common-dir')).resolve()
        receipt = read_json(self.common / 'sop/autotrigger.json')
        require(receipt.get('status') == 'complete' and receipt.get('commit') == self.head,
                'previous SOP release is incomplete or source changed')
        self.policy = read_json(self.repo / 'deploy/ga401-upgrade/transition.json')
        require(SHA.fullmatch(self.policy.get('upstream_commit', '')), 'missing upstream binding')
        self.git('merge-base', '--is-ancestor', self.policy['upstream_commit'], self.head)

    def snapshot(self):
        # Execute the installed, reviewed read-only owner, not code from a future merge.
        code = Path(__file__).with_name('upgrade.py').read_text(encoding='utf-8')
        command = 'python3 - snapshot --source-commit ' + self.head
        return json.loads(self.command(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', 'ga401', command], data=code, timeout=60))

    def prepare(self, release, previous):
        target = release['upstream_sha']
        require(version(release['tag']) > version(self.policy['target_version']), 'upstream tag moved or version did not increase')
        self.git('fetch', '--no-tags', REMOTE, target)
        require(self.git('rev-parse', 'FETCH_HEAD') == target, 'fetched source differs from discovered release')
        self.git('merge-base', '--is-ancestor', self.policy['upstream_commit'], target)
        paths = self.git('diff', '--name-only', self.policy['upstream_commit'], target).splitlines()
        require(not protected(paths), 'upstream changed automation or instruction owners; manual review required')
        self.git('merge', '--no-ff', '--no-commit', target)
        require(self.git('rev-parse', 'MERGE_HEAD') == target, 'real merge parent missing')
        transition = {'prior_commit': self.head, 'prior_ledger': previous['ledger'],
                      'prior_images': previous['images'], 'target_version': release['tag'][1:],
                      'upstream_commit': target}
        write_json(self.repo / 'deploy/ga401-upgrade/transition.json', transition)
        self.git('add', '--', 'deploy/ga401-upgrade/transition.json')
        require(not self.git('diff', '--name-only'), 'unstaged source changes after merge')
        self.tree = self.git('write-tree')

    def checks(self):
        # Preparation is explicit; SOP readiness itself remains read-only.
        for mode in ('prepare', 'quick', 'full'):
            self.command([sys.executable, '-B', 'deploy/ga401-upgrade/controller.py', mode])
        require(self.git('write-tree') == self.tree and not self.git('diff', '--name-only'), 'checks changed frozen source')

    def review(self):
        # A server release affects shared infrastructure with potentially new migrations.
        # The unchanged path above has no review admission and invokes no model.
        facts = ['--uncertainty', 'material', '--blast-radius', 'shared', '--verification', 'deterministic',
                 '--rollback', 'costly', '--failure-cost', 'material', '--data-boundary', 'sensitive',
                 '--scope-knowledge', 'known', '--change-kind', 'infrastructure', '--privilege-boundary', 'unchanged',
                 '--destructive', 'no', '--irreversibility', 'reversible', '--operational-controls', 'not_applicable',
                 '--project-policy', 'default', '--format', 'json']
        result = json.loads(self.command([sys.executable, '-B', str(SKILLS / 'risk-classification/scripts/risk-classification.py'), *facts]))
        write_json(self.root / 'risk.json', result)
        require(result.get('risk') == 'high' and result.get('formal_review') == 'required', 'unexpected review admission')
        quota_input = self.root / ('review-quota-' + uuid.uuid4().hex + '.json')
        preparation = [sys.executable, '-B', str(SKILLS / 'native-review/scripts/prepare-quota.py'),
            '--task', 'Review a complete Multica upstream merge for GA401 compatibility, migrations and custom feature preservation',
            '--allow-gemini', '--output', str(quota_input)]
        claude_input = self.root / 'claude-quota-input.json'
        if claude_input.exists() or claude_input.is_symlink():
            preparation.extend(['--claude-input', str(claude_input)])
        # A full upstream merge is not automatically admitted to a smaller reviewer.
        prepared = json.loads(self.command(preparation, timeout=120))
        require(prepared.get('status') == 'selected' and prepared.get('input') == str(quota_input),
                'no fresh task-eligible quota; no reviewer was invoked')
        output = self.command([sys.executable, '-B', str(SKILLS / 'native-review/scripts/native-review.py'),
            '--repo', str(self.repo), '--scope', 'staged', '--quota-input', str(quota_input),
            '--criteria', 'Preserve slash skill selection, native voice, invite-only access, accounts, provider configuration and durable data; detect upstream compatibility and migration failures before GA401 activation.',
            '--validation', 'Canonical custom Desktop quick/full gates, upgrade controller tests and real invite-only DB tests passed on the frozen staged tree.',
            '--accept-external-review'], timeout=7500)
        selection, emitted = quota_review_result(output)
        packet = emitted.get('packet_sha256', '')
        require(re.fullmatch(r'[0-9a-f]{64}', packet), 'review packet identity missing')
        evidence = self.common / 'sop/claude-reviews' / packet / 'report.json'
        report = read_json(evidence)
        validate_review(report, self.head)
        chosen = selection['selected']
        require(len(report['attempts']) == 1 and report['attempts'][0].get('reviewer') == chosen['route']
                and report['attempts'][0].get('selected_model') == chosen['model'],
                'native reviewer differs from the quota selection')
        require(read_json(evidence.with_name('quota-selection.json')).get('input_sha256') == selection['input_sha256'],
                'archived quota input binding changed')
        require(self.git('write-tree') == self.tree and not self.git('diff', '--name-only'), 'source changed after review')
        self.state['review_packet'] = packet

    def release(self):
        inspect = self.sop('autotrigger', '--inspect')
        require(inspect.get('action') == 'start' and inspect.get('ready') is True, 'SOP refused a new release')
        launched = self.sop('release-runner', '--launch', '--commit-message', 'chore: sync official Multica release preserving custom features')
        run_id = launched['run']['run_id']
        self.save('running', stage='release', run_id=run_id)
        while True:
            observed = self.sop('release-runner', '--wait', '--run-id', run_id, '--timeout', '50')
            state = observed['run']['status']
            if state == 'running': continue
            require(state == 'complete' and observed['run'].get('returncode') == 0, 'SOP release failed; explicit resume required')
            break
        self.head = self.git('rev-parse', 'HEAD')
        require(self.git('rev-parse', 'HEAD^2') == self.state['upstream_sha'], 'published merge lost upstream parent')
        self.snapshot()

    def execute(self, discoverer=discover):
        require(self.state.get('status') not in TERMINAL, 'previous failed/interrupted cycle requires operator decision')
        try:
            self.baseline()
            release = discoverer(self.policy['upstream_commit'])
            details = {key: value for key, value in release.items() if key != 'status'}
            if release['status'] == 'unchanged':
                self.save('unchanged', source_commit=self.head, **details)
                return self.state
            self.save('running', stage='snapshot', source_commit=self.head, **details)
            previous = self.snapshot()
            require(previous['source_commit'] == self.head, 'live source drift')
            for stage, action in (('merge', lambda: self.prepare(release, previous)),
                                  ('checks', self.checks), ('review', self.review), ('release', self.release)):
                self.save('running', stage=stage)
                action()
            self.save('complete', stage='complete', source_commit=self.head)
            return self.state
        except Exception as exc:
            self.save('needs_attention', error=str(exc))
            raise

def main():
    p = argparse.ArgumentParser(); p.add_argument('--repo', required=True, type=Path); p.add_argument('--state-root', required=True, type=Path)
    args = p.parse_args()
    args.state_root.mkdir(parents=True, exist_ok=True)
    try:
        with lock(args.state_root / 'cycle.lock'):
            result = Cycle(args.repo, args.state_root).execute()
        print(json.dumps(result)); return 0
    except Exception as exc:
        print('NEEDS ATTENTION: ' + str(exc), file=sys.stderr); return 1

if __name__ == '__main__': raise SystemExit(main())
