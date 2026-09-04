"""Extract immutable committed scheduler owners; never copy worktree edits."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

NAMES = ('cycle.py', 'discover.py', 'upgrade.py')
BRANCH = 'codex/ga401-upgrade-0439'

def git(repo, *args):
    p = subprocess.run(['git', '-C', str(repo), *args], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if p.returncode: raise RuntimeError('committed source extraction failed')
    return p.stdout

def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def verify(directory, commit):
    value = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if value.get('commit') != commit or set(value.get('files', {})) != set(NAMES):
        raise RuntimeError('installed manifest identity mismatch')
    for name in NAMES:
        if digest(directory / name) != value['files'][name]: raise RuntimeError('installed file integrity mismatch')
    return value

def install(repo, commit, root):
    if not re.fullmatch(r'[0-9a-f]{40}', commit): raise RuntimeError('exact commit required')
    if git(repo, 'rev-parse', 'HEAD').decode().strip() != commit: raise RuntimeError('HEAD drift')
    if git(repo, 'branch', '--show-current').decode().strip() != BRANCH: raise RuntimeError('branch drift')
    remote = git(repo, 'remote', 'get-url', 'origin').decode().strip()
    if remote.removesuffix('.git') not in ('https://github.com/medking82/multica', 'git@github.com:medking82/multica'):
        raise RuntimeError('origin drift')
    versions = root / 'versions'; versions.mkdir(parents=True, exist_ok=True)
    final = versions / commit
    if final.exists(): verify(final, commit); return final
    temporary = versions / ('.install-' + uuid.uuid4().hex); temporary.mkdir()
    for name in NAMES:
        (temporary / name).write_bytes(git(repo, 'cat-file', 'blob', f'{commit}:deploy/ga401-upgrade/{name}'))
    manifest = {'commit': commit, 'repo': str(repo), 'branch': BRANCH,
                'files': {name: digest(temporary / name) for name in NAMES}}
    (temporary / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    verify(temporary, commit)
    temporary.rename(final)
    return final

def main():
    p=argparse.ArgumentParser(); p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--commit', required=True); p.add_argument('--root', type=Path, required=True)
    args=p.parse_args(); print(install(args.repo.resolve(), args.commit, args.root.resolve()))

if __name__ == '__main__': main()
