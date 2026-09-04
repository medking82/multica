"""One-shot GA401 cutover from an exact committed source archive.

Failed/interrupted phases are terminal. There is no retry, resume, cleanup or
rollback command. Private backups and old images remain for operator recovery.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.request

APP = Path('/home/marck/services/multica/app')
RUNTIME = Path('/home/marck/services/multica-runtime/releases/browser-update-20260831-1/browser-update')
BASE = Path('/home/marck/services/multica/upgrades')
NAMES = {'backend': 'multica-backend-1', 'frontend': 'multica-frontend-1',
         'postgres': 'multica-postgres-1', 'runtime': 'multica-ga401-runtime-runtime-1'}
OLD = {'backend': 'sha256:da3c239ecb737b52db621e9707d9310b8c879b959bd8ca81fadb081c39825189',
       'frontend': 'sha256:ec2f3ddf1bbaaca98431e8114bbecca2eff7804a9539069e6ee0230f4fe6f1ec',
       'runtime': 'sha256:fbebe860e8ab842e3a5f98e20383752fb7a64e32f6877b01e8641e83b03fa13f'}
VOLUMES = {'postgres': {'/var/lib/postgresql/data': 'multica_pgdata'},
           'backend': {'/app/data/uploads': 'multica_backend_uploads'},
           'runtime': {'/home/agent': 'multica-ga401-runtime_runtime-home',
                       '/opt/agent-tools': 'multica-ga401-runtime_cli-tools',
                       '/opt/browser-tools': 'multica-ga401-runtime_browser-tools'}}
TABLES = ('user', 'workspace', 'member', 'agent', 'agent_runtime', 'issue', 'comment', 'project', 'skill')
PHASES = ('preflight', 'build', 'rehearse', 'activate', 'verify')


class Failure(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Failure(message)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def file_hash(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(args, *, data=None, output=None, merge_log=False):
    # Never print arguments or raw errors: compose/DB input can contain secrets.
    result = subprocess.run(args, input=data, stdout=output or subprocess.PIPE,
                            stderr=output if merge_log else subprocess.PIPE)
    require(result.returncode == 0, f'{args[0]} failed (exit {result.returncode}; stderr sha256 {digest(result.stderr or b"")})')
    return result.stdout or b''


def docker(*args, **kwargs):
    return run(['docker', *args], **kwargs)


def inspect(name, kind='container'):
    return json.loads(docker(kind, 'inspect', name))[0]


def http(url):
    with urllib.request.urlopen(url, timeout=15) as response:
        require(response.status == 200, 'HTTP health check failed')
        return response.read()


def mounts(container):
    return {item['Destination']: [item['Type'], item.get('Name', item.get('Source')), item['RW']]
            for item in container['Mounts']}


def container_contract(container):
    return {'mounts': mounts(container), 'ports': container['HostConfig']['PortBindings'],
            'user': container['Config']['User'], 'network': container['HostConfig']['NetworkMode']}


def compose_config(directory, filename, project):
    return json.loads(docker('compose', '--project-directory', str(directory), '-p', project,
                             '-f', str(directory / filename), 'config', '--format', 'json'))


def config_hashes():
    configs = [compose_config(APP, 'docker-compose.selfhost.yml', 'multica'),
               compose_config(RUNTIME, 'compose.yaml', 'multica-ga401-runtime')]
    return [digest(json.dumps(c, sort_keys=True).encode()) for c in configs]


def db_identity():
    env = dict(item.split('=', 1) for item in inspect(NAMES['postgres'])['Config']['Env'])
    user = env.get('POSTGRES_USER', 'postgres')
    database = env.get('POSTGRES_DB', user)
    require(re.fullmatch(r'[a-zA-Z0-9_]+', user) and re.fullmatch(r'[a-zA-Z0-9_]+', database), 'unexpected database identity')
    return user, database


def sql(query, container=None, user=None, database=None):
    if user is None:
        user, database = db_identity()
    return docker('exec', '-i', container or NAMES['postgres'], 'psql', '-X', '-v', 'ON_ERROR_STOP=1',
                  '-U', user, '-d', database, '-At', data=query.encode()).decode().strip()


def ledger(**db):
    return sql('SELECT max(version) FROM schema_migrations;', **db)


def identities(**db):
    # Durable row identities only; no account or task contents in the receipt.
    query = ' UNION ALL '.join(
        f"SELECT '{table}', count(*), md5(coalesce(string_agg(id::text, ',' ORDER BY id::text), '')) FROM \"{table}\""
        for table in TABLES) + ';'
    return sql(query, **db)


def idle():
    require(sql("SELECT count(*) FROM agent_task_queue WHERE status NOT IN ('completed','failed','cancelled');") == '0',
            'active or unknown task queue states')
    require(sql('SELECT count(*) FROM autopilot_trigger;') == '0', 'autopilot trigger requires separate migration review')
    # Read process names, never provider argument strings or authentication.
    processes = docker('exec', NAMES['runtime'], 'ps', '-eo', 'comm=').decode().splitlines()
    require(not any(re.search(r'^(codex|claude|agy|gemini|qwen|pi)(\.exe)?$', p.strip(), re.I)
                    for p in processes), 'active provider process')


def health(commit):
    require(json.loads(http('http://127.0.0.1:8080/health')).get('commit') == commit, 'backend source mismatch')
    ready = json.loads(http('http://127.0.0.1:8080/readyz'))
    require(ready.get('status') == 'ok' and ready.get('checks', {}).get('db') == 'ok'
            and ready.get('checks', {}).get('migrations') == 'ok', 'backend not ready')


class Upgrade:
    def __init__(self, root, commit):
        require(re.fullmatch(r'[0-9a-f]{40}', commit), 'exact source commit required')
        require(root == BASE / commit and root.resolve() == root, 'unexpected release root')
        self.root, self.commit = root, commit
        self.path = root / 'state.json'
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {'completed': []}
        require(self.state.get('commit', commit) == commit, 'state source mismatch')
        self.state['commit'] = commit
        self.source = root / 'source'
        self.version = '0.4.39-ga401.' + commit[:8]
        self.tags = {role: 'multica-ga401-' + role + ':' + self.version for role in OLD}

    def save(self):
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.state, indent=2, sort_keys=True) + '\n')
        temporary.chmod(0o600)
        os.replace(temporary, self.path)

    def phase(self, name):
        require(self.state.get('status') != 'FAILED_NEEDS_DECISION', 'previous failure requires operator decision')
        require(self.state['completed'] == list(PHASES[:PHASES.index(name)]), 'phase order or duplicate phase refused')
        require(not self.state.get('running'), 'interrupted phase requires operator decision')
        self.state['running'] = name
        self.save()
        try:
            getattr(self, name)()
            self.state['completed'].append(name)
            self.state['running'] = None
            self.state['status'] = 'complete' if name == 'verify' else 'ready'
            self.save()
            print(json.dumps({'phase': name, 'status': 'complete', 'commit': self.commit}), flush=True)
        except Exception as exc:
            self.state['status'] = 'FAILED_NEEDS_DECISION'
            self.state['failure'] = {'phase': name, 'type': type(exc).__name__, 'message': str(exc)}
            self.save()
            raise

    def originals(self):
        require(config_hashes() == self.state['config_hashes'], 'compose or environment changed')
        for role, image in OLD.items():
            current = inspect(NAMES[role])
            require(current['Image'] == image and current['Id'] == self.state['containers'][role]['id'], 'original container changed')

    def preflight(self):
        require(os.uname().nodename == 'Marck-ROG-Zephyrus-G14-GA401QM', 'wrong host')
        require(shutil.disk_usage(self.root).free > 40 * 1024**3, 'less than 40 GiB free')
        require(ledger().startswith('440_'), 'expected original migration 440')
        health('560f01203-invite-only')
        idle()
        self.state['config_hashes'] = config_hashes()
        self.state['containers'] = {}
        for role, name in NAMES.items():
            current = inspect(name)
            require(current['State']['Running'], f'{role} is not running')
            if role in OLD:
                require(current['Image'] == OLD[role], 'original image mismatch')
            project = 'multica-ga401-runtime' if role == 'runtime' else 'multica'
            require(current['Config']['Labels'].get('com.docker.compose.project') == project, 'wrong compose owner')
            for target, volume in VOLUMES.get(role, {}).items():
                require(mounts(current).get(target, [None, None])[:2] == ['volume', volume], 'production volume mismatch')
            self.state['containers'][role] = {'id': current['Id'], 'image': current['Image'], 'contract': container_contract(current)}
        env = dict(item.split('=', 1) for item in inspect(NAMES['backend'])['Config']['Env'])
        require(env.get('ALLOW_SIGNUP') == 'false', 'invite-only configuration must remain false')
        self.state['before_ids'] = identities()

    def build(self):
        self.originals()
        require((self.source / 'Dockerfile').is_file(), 'missing committed source')
        for role, filename, arguments in (
            ('backend', 'Dockerfile', ['--build-arg', 'VERSION=' + self.version, '--build-arg', 'COMMIT=' + self.commit]),
            ('frontend', 'Dockerfile.web', ['--build-arg', 'NEXT_PUBLIC_APP_VERSION=' + self.version])):
            print('Building ' + role, flush=True)
            with (self.root / (role + '-build.log')).open('xb') as log:
                docker('build', '--pull=false', '-f', str(self.source / filename), '-t', self.tags[role],
                       *arguments, str(self.source), output=log, merge_log=True)
        runtime_dir = self.root / 'runtime-build'
        runtime_dir.mkdir(mode=0o700)
        (runtime_dir / 'Dockerfile').write_text('FROM ' + OLD['runtime'] + '\nCOPY --from=' + self.tags['backend'] +
                                               ' /app/multica /usr/local/bin/multica\n')
        with (self.root / 'runtime-build.log').open('xb') as log:
            docker('build', '--pull=false', '-t', self.tags['runtime'], str(runtime_dir), output=log, merge_log=True)
        self.state['images'] = {role: inspect(tag, 'image')['Id'] for role, tag in self.tags.items()}
        for role, binary in (('backend', '/app/multica'), ('runtime', '/usr/local/bin/multica')):
            version = docker('run', '--rm', '--network', 'none', '--read-only', '--tmpfs', '/tmp',
                             '-e', 'HOME=/tmp/empty', '--entrypoint', binary, self.tags[role], '--version').decode()
            require(self.version in version and self.commit[:8] in version, 'candidate CLI identity mismatch')
        require(inspect(OLD['runtime'], 'image')['Config'] == inspect(self.tags['runtime'], 'image')['Config'],
                'derived runtime changed provider or execution configuration')

    def dump(self, filename):
        path = self.root / filename
        user, database = db_identity()
        with path.open('xb') as output:
            docker('exec', NAMES['postgres'], 'pg_dump', '-Fc', '-U', user, '-d', database, output=output)
        path.chmod(0o600)
        require(path.stat().st_size > 1024, 'database backup is unexpectedly empty')
        with path.open('rb') as stream:
            toc = docker('exec', '-i', NAMES['postgres'], 'pg_restore', '--list', data=stream.read())
        require(b'TABLE DATA' in toc, 'database backup has no table data')
        return path

    def rehearse(self):
        self.originals()
        dump = self.dump('rehearsal.dump')
        suffix = self.commit[:12]
        network, container = 'multica-rehearse-' + suffix, 'multica-rehearse-pg-' + suffix
        docker('network', 'create', '--internal', '--label', 'io.hankee.upgrade=' + self.commit, network)
        image = self.state['containers']['postgres']['image']
        docker('run', '-d', '--name', container, '--network', network, '--label', 'io.hankee.upgrade=' + self.commit,
               '--tmpfs', '/var/lib/postgresql/data:rw,size=8g', '-e', 'POSTGRES_HOST_AUTH_METHOD=trust',
               '-e', 'POSTGRES_USER=rehearsal', '-e', 'POSTGRES_DB=rehearsal', image)
        observed = inspect(container)
        require(not observed['HostConfig']['PortBindings'] and all(m['Type'] == 'tmpfs' for m in observed['Mounts']),
                'rehearsal has a persistent mount or published port')
        self.state['rehearsal_container'] = container
        self.save()
        # Observations wait for startup; they never repeat a failed restore/migration.
        for _ in range(60):
            probe = subprocess.run(['docker', 'exec', container, 'pg_isready', '-U', 'rehearsal'], capture_output=True)
            if probe.returncode == 0:
                break
            time.sleep(1)
        else:
            raise Failure('rehearsal database readiness timeout')
        with dump.open('rb') as stream:
            docker('exec', '-i', container, 'pg_restore', '--exit-on-error', '--no-owner', '--no-privileges',
                   '-U', 'rehearsal', '-d', 'rehearsal', data=stream.read())
        db = {'container': container, 'user': 'rehearsal', 'database': 'rehearsal'}
        before = identities(**db)
        require(ledger(**db).startswith('440_'), 'restored database has unexpected schema')
        docker('run', '--rm', '--network', network, '--read-only', '--entrypoint', '/app/migrate',
               '-e', 'DATABASE_URL=postgres://rehearsal@' + container + ':5432/rehearsal?sslmode=disable',
               self.tags['backend'], 'up')
        require(ledger(**db) == ledger_name(self.source), 'rehearsal migration incomplete')
        require(identities(**db) == before, 'migration changed durable data identities')
        docker('stop', container)
        self.state['rehearsal'] = {'dump_sha256': file_hash(dump), 'ledger': ledger_name(self.source),
                                   'identities_sha256': digest(before.encode())}

    def compose(self, runtime, *args):
        directory, filename, project = (RUNTIME, 'compose.yaml', 'multica-ga401-runtime') if runtime else (APP, 'docker-compose.selfhost.yml', 'multica')
        override = self.root / ('runtime-image.json' if runtime else 'app-images.json')
        return docker('compose', '--project-directory', str(directory), '-p', project, '-f', str(directory / filename),
                      '-f', str(override), *args)

    def activate(self):
        self.originals()
        idle()
        require(ledger().startswith('440_'), 'production schema changed before cutover')
        for role, image in self.state['images'].items():
            require(inspect(self.tags[role], 'image')['Id'] == image, 'candidate tag moved')
        backup = self.root / 'backups'
        backup.mkdir(mode=0o700)
        for prefix, directory, filename in [('app', APP, 'docker-compose.selfhost.yml'), ('runtime', RUNTIME, 'compose.yaml')]:
            for leaf in (filename, '.env'):
                source = directory / leaf
                if source.exists():
                    target = backup / (prefix + '-' + leaf)
                    shutil.copyfile(source, target)
                    target.chmod(0o600)
        (self.root / 'app-images.json').write_text(json.dumps({'services': {r: {'image': self.state['images'][r]}
                                                                         for r in ('backend', 'frontend')}}))
        (self.root / 'runtime-image.json').write_text(json.dumps({'services': {'runtime': {'image': self.state['images']['runtime']}}}))
        # Effective overlays must preserve everything except the intended image values.
        for runtime, directory, filename, project in [(False, APP, 'docker-compose.selfhost.yml', 'multica'),
                                                     (True, RUNTIME, 'compose.yaml', 'multica-ga401-runtime')]:
            before = compose_config(directory, filename, project)
            after = json.loads(self.compose(runtime, 'config', '--format', 'json'))
            for role in (('runtime',) if runtime else ('backend', 'frontend')):
                after['services'][role]['image'] = before['services'][role]['image']
            require(before == after, 'image override changes non-image configuration')
        idle()
        docker('stop', NAMES['runtime'], NAMES['frontend'], NAMES['backend'])
        self.state['writers_stopped'] = True
        self.save()
        require(sql("SELECT count(*) FROM agent_task_queue WHERE status NOT IN ('completed','failed','cancelled');") == '0',
                'new task arrived during quiescence; operator decision required')
        self.state['final_ids'] = identities()
        self.state['final_dump_sha256'] = file_hash(self.dump('final.dump'))
        for volume in ('multica_backend_uploads', 'multica-ga401-runtime_runtime-home'):
            docker('run', '--rm', '--network', 'none', '--read-only', '--mount', 'type=volume,src=' + volume + ',dst=/data,readonly',
                   '--mount', 'type=bind,src=' + str(backup) + ',dst=/backup', 'alpine:3.21',
                   'sh', '-c', 'umask 077; tar -C /data -cpf /backup/' + volume + '.tar . && tar -tf /backup/' + volume + '.tar >/dev/null')
        self.save()
        self.compose(False, 'up', '-d', '--no-deps', '--no-build', '--pull', 'never', 'backend', 'frontend')
        self.wait_health()
        require(identities() == self.state['final_ids'], 'post-migration data identities changed')
        self.verify_containers(runtime=False)
        self.compose(True, 'up', '-d', '--no-deps', '--no-build', '--pull', 'never', 'runtime')
        self.state['writers_stopped'] = False

    def wait_health(self):
        for _ in range(120):
            require(inspect(NAMES['backend'])['State']['Running'], 'candidate backend exited')
            try:
                health(self.commit)
                return
            except (OSError, ValueError, Failure):
                time.sleep(1)
        raise Failure('candidate health readiness timeout')

    def verify_containers(self, runtime=True):
        require(inspect(NAMES['postgres'])['Id'] == self.state['containers']['postgres']['id'], 'postgres was replaced')
        require(config_hashes() == self.state['config_hashes'], 'original compose/environment drift')
        for role in (('backend', 'frontend', 'runtime') if runtime else ('backend', 'frontend')):
            current = inspect(NAMES[role])
            require(current['Image'] == self.state['images'][role] and current['State']['Running'], 'candidate not running')
            require(container_contract(current) == self.state['containers'][role]['contract'], 'container contract changed')

    def verify(self):
        health(self.commit)
        require(ledger() == ledger_name(self.source), 'final migration ledger mismatch')
        self.verify_containers()
        env = dict(item.split('=', 1) for item in inspect(NAMES['backend'])['Config']['Env'])
        require(env.get('ALLOW_SIGNUP') == 'false', 'invite-only config changed')
        version = docker('exec', NAMES['runtime'], '/usr/local/bin/multica', '--version').decode()
        require(self.version in version and self.commit[:8] in version, 'runtime CLI version mismatch')
        require(identities() == self.state['final_ids'], 'data identities changed during upgrade')
        for path in ('/', '/health', '/readyz'):
            body = http('https://agent.hankee.com' + path)
            if path == '/health':
                require(json.loads(body).get('commit') == self.commit, 'public route has wrong source')
        self.state['verified_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def ledger_name(source):
    return sorted(p.name.removesuffix('.up.sql') for p in (source / 'server/migrations').glob('*.up.sql'))[-1]


def main():
    import fcntl  # Remote-only; unit tests also run on Windows.
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=PHASES)
    parser.add_argument('--release-root', type=Path, required=True)
    parser.add_argument('--source-commit', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    with (BASE / '.upgrade.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        Upgrade(args.release_root, args.source_commit).phase(args.phase)


if __name__ == '__main__':
    main()
