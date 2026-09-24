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
import tarfile
import time
import urllib.request

APP = Path('/home/marck/services/multica/app')
RUNTIME = Path('/home/marck/services/multica-runtime/releases/runtime-candidate-20260923-2/browser-update')
BASE = Path('/home/marck/services/multica/upgrades')
NAMES = {'backend': 'multica-backend-1', 'frontend': 'multica-frontend-1',
         'postgres': 'multica-postgres-1', 'runtime': 'multica-ga401-runtime-runtime-1'}
# The previous cutover succeeded; only its public probe was rejected by BIC.
# Resume binds the verified deployed images and schema, never the obsolete 440 DB.
OLD_COMMIT = 'daad4487937f5cf493f8705c23659d5c48a3055d'
OLD_LEDGER = '450_drop_comment_delegated_failure_pending_index'
OLD = {'backend': 'sha256:205c33c5762f092c685688df88385f7693678bb062fe4af63dfef027133a364f',
       'frontend': 'sha256:158f0ab72e63e2b883fe3c172b3d4915d7a296b6995084cf8161be5907bb7da3',
       'runtime': 'sha256:9d5bc992520f52f021aa84a48ba1ec47fc10a76162ce4be4ef96564ee0846337'}
VOLUMES = {'postgres': {'/var/lib/postgresql/data': 'multica_pgdata'},
           'backend': {'/app/data/uploads': 'multica_backend_uploads'},
           'runtime': {'/home/agent': 'multica-ga401-runtime_runtime-home',
                       '/opt/agent-tools': 'multica-ga401-runtime_cli-tools',
                       '/opt/browser-tools': 'multica-ga401-runtime_browser-tools'}}
TABLES = ('user', 'workspace', 'member', 'agent', 'agent_runtime', 'issue', 'comment', 'project', 'skill')
PHASES = ('preflight', 'build', 'rehearse', 'activate', 'verify')
HEX40 = re.compile(r'^[0-9a-f]{40}$')
TRANSITION_FIELDS = {'prior_commit', 'prior_ledger', 'prior_images', 'target_version', 'upstream_commit',
                     'source_commit', 'source_tree', 'source_archive_sha256',
                     'deployment_tool_commit', 'deployment_tool_sha256'}
VERSION = re.compile(r'^\d+\.\d+\.\d+(?:-custom\.\d+)?$')


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


def verify_extracted_archive(archive, directory):
    """Require the extracted source tree to match the bound git archive byte-for-byte."""
    expected_files = set()
    with tarfile.open(archive, 'r:') as bundle:
        members = bundle.getmembers()
        for item in members:
            name = item.name.rstrip('/')
            require(name == 'source' or name.startswith('source/')
                    and '..' not in name.split('/') and not item.issym() and not item.islnk(),
                    'unsafe source archive entry')
            if name != 'source' and item.isfile():
                expected_files.add(name)
            target = directory / name
            if item.isdir():
                require(target.is_dir() and not target.is_symlink(), 'source directory differs from archive')
            elif item.isfile():
                require(target.is_file() and not target.is_symlink(), 'source file differs from archive')
                source = bundle.extractfile(item)
                require(source is not None, 'source archive file unreadable')
                hasher = hashlib.sha256()
                while chunk := source.read(1024 * 1024):
                    hasher.update(chunk)
                require(file_hash(target) == hasher.hexdigest(), 'extracted source content differs from archive')
            else:
                raise Failure('unsupported source archive entry')
    observed_files = set()
    source = directory / 'source'
    require(source.is_dir() and not source.is_symlink(), 'extracted source root missing')
    for path in source.rglob('*'):
        require(not path.is_symlink(), 'unexpected extracted source link')
        if path.is_file():
            observed_files.add(path.relative_to(directory).as_posix())
    require(observed_files == expected_files, 'extracted source tree differs from archive manifest')


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


def runtime_base_tag():
    # Compose may record Config.Image as an ID after an immutable image override.
    tags = inspect(OLD['runtime'], 'image').get('RepoTags') or []
    tags = sorted(tag for tag in tags if tag.startswith('multica-ga401-runtime:'))
    require(tags, 'runtime base has no retained local tag')
    require(inspect(tags[0], 'image')['Id'] == OLD['runtime'], 'runtime base tag drift')
    return tags[0]


def http(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'MulticaGA401HealthCheck/1.0'})
    with urllib.request.urlopen(request, timeout=15) as response:
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


def migration_snapshot(source, before, **db):
    """Aggregate only migration safety metadata; never return row values."""
    history = sql("SELECT coalesce(string_agg(version, E'\\n' ORDER BY version), '') FROM schema_migrations;", **db).splitlines()
    entity_ids = identities(**db)
    content = {}
    for table, excluded in (('issue', ['triage_state', 'duplicate_of_issue_id']),
                            ('comment', ['comment_thread_id', 'deleted_at']),
                            ('project', []), ('skill', [])):
        minus = " - ARRAY[" + ','.join("'" + key + "'" for key in excluded) + "]" if excluded else ''
        content[table] = sql(
            f"SELECT md5(coalesce(string_agg((to_jsonb(t){minus})::text, E'\\n' ORDER BY t.id::text), '')) FROM \"{table}\" t;", **db)
    categories = sql("SELECT coalesce(string_agg(category || ':' || n::text, ',' ORDER BY category), '') "
                     "FROM (SELECT category,count(*) n FROM issue_status GROUP BY category) s;", **db)
    links = {}
    for table in ('issue_pull_request', 'issue_vcs_pull_request'):
        if before:
            value = sql(f"SELECT count(*) FILTER (WHERE reference_only)::text || '|' || count(*)::text || '|' || "
                        f"md5(coalesce(string_agg(issue_id::text || ':' || pull_request_id::text, ',' ORDER BY issue_id::text,pull_request_id::text) "
                        f"FILTER (WHERE NOT reference_only),'')) FROM {table};", **db)
        else:
            value = sql(f"SELECT count(*)::text || '|' || md5(coalesce(string_agg(issue_id::text || ':' || pull_request_id::text, ',' "
                        f"ORDER BY issue_id::text,pull_request_id::text),'')) FROM {table};", **db)
        links[table] = value
    eligible_trigger = sql(
        "SELECT count(*)::text || '|' || md5(coalesce(string_agg(t.id::text || ':' || coalesce(t.created_by_type,'<null>') || ':' || "
        "coalesce(t.created_by_id::text,'<null>'), ',' ORDER BY t.id::text),'')) "
        "FROM autopilot_trigger t JOIN autopilot a ON a.id=t.autopilot_id "
        "WHERE (t.created_by_id IS NULL OR t.created_by_type IS DISTINCT FROM 'member') "
        "AND a.created_by_type='member' AND a.created_by_id IS NOT NULL "
        "AND EXISTS (SELECT 1 FROM member m WHERE m.user_id=a.created_by_id AND m.workspace_id=a.workspace_id);", **db)
    task_statuses = sql("SELECT coalesce(string_agg(status || ':' || started::text || ':' || n::text, ',' "
                        "ORDER BY status,started), '') FROM (SELECT status,(started_at IS NOT NULL) started,count(*) n "
                        "FROM agent_task_queue GROUP BY status,started_at IS NOT NULL) s;", **db)
    task_ids = sql("SELECT md5(coalesce(string_agg(id::text, ',' ORDER BY id::text),'')) FROM agent_task_queue;", **db)
    eligible_legacy = sql(
        "WITH RECURSIVE fallback_lineage AS (SELECT task.id AS task_id,task.escalation_for_task_id,ARRAY[task.id] AS path "
        "FROM agent_task_queue task WHERE task.escalation_for_task_id IS NOT NULL AND task.retry_of_task_id IS NULL "
        "UNION ALL SELECT retry.id,lineage.escalation_for_task_id,lineage.path || retry.id FROM fallback_lineage lineage "
        "JOIN agent_task_queue retry ON retry.parent_task_id=lineage.task_id AND retry.retry_of_task_id=lineage.task_id "
        "WHERE NOT retry.id=ANY(lineage.path)) SELECT count(*) FROM fallback_lineage lineage JOIN agent_task_queue task ON task.id=lineage.task_id "
        "WHERE task.started_at IS NULL AND cardinality(task.coalesced_comment_ids)=0 "
        "AND task.status IN ('deferred','queued','dispatched','waiting_local_directory');", **db)
    agent_statuses = sql("SELECT coalesce(string_agg(status || ':' || n::text, ',' ORDER BY status), '') "
                         "FROM (SELECT status,count(*) n FROM agent GROUP BY status) s;", **db)
    comment_delivery = sql("SELECT to_regclass('public.comment_agent_delivery') IS NOT NULL;", **db) == 't'
    comment_delivery_rows = int(sql('SELECT count(*) FROM comment_agent_delivery;', **db)) if comment_delivery else None
    new_tables = {}
    for table in ('maintenance_job', 'channel_reply_delivery', 'issue_wakeup', 'issue_wakeup_receipt',
                  'task_supplement_capability', 'task_supplement', 'instance_telemetry_state'):
        exists = sql("SELECT to_regclass('public." + table + "') IS NOT NULL;", **db) == 't'
        new_tables[table] = int(sql(f'SELECT count(*) FROM {table};', **db)) if exists else None
    expected_history = sorted(p.name.removesuffix('.up.sql') for p in (source / 'server/migrations').glob('*.up.sql'))
    return {'schema_history': history, 'candidate_migrations': expected_history,
            'entity_ids': entity_ids, 'content_hashes': content, 'issue_status_categories': categories,
            'pr_links': links, 'eligible_autopilot_triggers': eligible_trigger,
            'task_ids': task_ids, 'task_statuses': task_statuses, 'eligible_legacy_fallback_tasks': eligible_legacy,
            'agent_statuses': agent_statuses, 'comment_agent_delivery_exists': comment_delivery,
            'comment_agent_delivery_rows': comment_delivery_rows, 'new_tables': new_tables}


def validate_migration_snapshots(before, after):
    baseline = set(before['schema_history'])
    target = set(after['schema_history'])
    source = set(after['candidate_migrations'])
    require(baseline, 'pre-migration schema ledger is empty')
    old_max = max(int(version.split('_', 1)[0]) for version in baseline)
    expected_baseline = {version for version in source if int(version.split('_', 1)[0]) <= old_max}
    require(baseline == expected_baseline and target == source,
            'migration ledger differs from exact source migration set')
    require(before['candidate_migrations'] == after['candidate_migrations'], 'candidate migration files changed')
    require(before['entity_ids'] == after['entity_ids'], 'durable entity IDs or counts changed')
    require(before['content_hashes'] == after['content_hashes'], 'preserved business content changed')
    require(before['task_ids'] == after['task_ids'], 'task queue IDs changed')
    require(before['task_statuses'] == after['task_statuses'], 'task status changed during migration-only checkpoint')
    require(before['eligible_legacy_fallback_tasks'] == '0' and after['eligible_legacy_fallback_tasks'] == '0',
            'migration 456 would rewrite queued task state')
    require(before['agent_statuses'] == after['agent_statuses'], 'agent status changed during migration-only checkpoint')
    require(before['eligible_autopilot_triggers'].startswith('0|') and after['eligible_autopilot_triggers'].startswith('0|'),
            'migration 467 would infer trigger principals')
    require(not before['comment_agent_delivery_exists'] and not after['comment_agent_delivery_exists'],
            'migration 534 would drop the comment delivery table')
    require(before['comment_agent_delivery_rows'] is None and after['comment_agent_delivery_rows'] is None,
            'unexpected comment delivery table data')
    for table in ('issue_pull_request', 'issue_vcs_pull_request'):
        old_reference, old_total, old_digest = before['pr_links'][table].split('|')
        new_total, new_digest = after['pr_links'][table].split('|')
        require(old_reference == '0' and new_total == old_total and new_digest == old_digest,
                'PR link migration would delete existing links')
    mapping = {'backlog': 'unstarted', 'todo': 'unstarted', 'in_progress': 'started',
               'in_review': 'started', 'blocked': 'started', 'cancelled': 'closed'}
    def categories(encoded):
        result = {}
        if encoded:
            for item in encoded.split(','):
                key, count = item.rsplit(':', 1)
                result[key] = int(count)
        return result
    expected = {}
    for category, count in categories(before['issue_status_categories']).items():
        normalized = mapping.get(category, category)
        expected[normalized] = expected.get(normalized, 0) + count
    require(categories(after['issue_status_categories']) == expected, 'issue status category normalization mismatch')
    require(all(value is None for value in before['new_tables'].values()), 'new migration-owned tables already exist')
    require(after['new_tables'] == {'maintenance_job': 0, 'channel_reply_delivery': 0, 'issue_wakeup': 0,
                                    'issue_wakeup_receipt': 0, 'task_supplement_capability': 0,
                                    'task_supplement': 0, 'instance_telemetry_state': 1},
            'unexpected migration-created table contents')


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
    def __init__(self, root, commit, transition_file=None, deployment_tool=None):
        require(re.fullmatch(r'[0-9a-f]{40}', commit), 'exact source commit required')
        require(root == BASE / commit and root.resolve() == root, 'unexpected release root')
        self.root, self.commit = root, commit
        transition_file = transition_file or root / 'transition.json'
        require(transition_file == root / 'transition.json', 'transition must be external task metadata')
        require(transition_file.is_file(), 'missing transition receipt')
        transition = json.loads(transition_file.read_text(encoding='utf-8'))
        require(isinstance(transition, dict) and set(transition) == TRANSITION_FIELDS, 'transition receipt fields mismatch')
        require(isinstance(transition['prior_commit'], str) and isinstance(transition['upstream_commit'], str)
                and HEX40.fullmatch(transition['prior_commit']) and HEX40.fullmatch(transition['upstream_commit']),
                'transition commit binding invalid')
        require(transition['prior_commit'] != commit and transition['upstream_commit'] != transition['prior_commit'],
                'transition source/previous commit mismatch')
        require(isinstance(transition['prior_ledger'], str) and transition['prior_ledger'], 'transition ledger binding invalid')
        require(isinstance(transition['prior_images'], dict) and set(transition['prior_images']) == {'backend', 'frontend', 'runtime'}, 'transition image binding invalid')
        require(all(isinstance(value, str) and re.fullmatch(r'sha256:[0-9a-f]{64}', value) for value in transition['prior_images'].values()), 'transition image ids invalid')
        require(isinstance(transition['target_version'], str) and VERSION.fullmatch(transition['target_version']), 'transition target version invalid')
        require(transition['source_commit'] == commit, 'transition product source mismatch')
        require(isinstance(transition['source_tree'], str) and HEX40.fullmatch(transition['source_tree']), 'transition source tree invalid')
        require(isinstance(transition['source_archive_sha256'], str)
                and re.fullmatch(r'[0-9a-f]{64}', transition['source_archive_sha256']), 'source archive digest invalid')
        require(isinstance(transition['deployment_tool_commit'], str)
                and HEX40.fullmatch(transition['deployment_tool_commit']), 'deployment tool commit invalid')
        require(isinstance(transition['deployment_tool_sha256'], str)
                and re.fullmatch(r'[0-9a-f]{64}', transition['deployment_tool_sha256']), 'deployment tool digest invalid')
        source_archive = root / 'source.tar'
        require(source_archive.is_file() and file_hash(source_archive) == transition['source_archive_sha256'],
                'source archive digest mismatch')
        verify_extracted_archive(source_archive, root)
        deployment_tool = deployment_tool or Path(__file__)
        require(file_hash(deployment_tool) == transition['deployment_tool_sha256'], 'deployment tool digest mismatch')
        transition_sha = file_hash(transition_file)
        self.transition = transition
        self.old_commit = transition['prior_commit']
        self.old_ledger = transition['prior_ledger']
        self.old_images = dict(transition['prior_images'])
        # The transition is created in the new release directory, while the
        # immutable completed receipt it refers to lives in the prior release.
        prior_root = BASE / self.old_commit
        require(prior_root.is_dir() and prior_root.resolve() == prior_root, 'prior release root missing')
        prior_path = prior_root / 'state.json'
        require(prior_path.is_file(), 'prior release receipt missing')
        prior_state = json.loads(prior_path.read_text(encoding='utf-8'))
        require(prior_state.get('status') == 'complete' and prior_state.get('completed') == list(PHASES), 'prior upgrade receipt is not complete')
        require(prior_state.get('commit') == self.old_commit, 'prior release commit receipt mismatch')
        prior_images = prior_state.get('images')
        require(isinstance(prior_images, dict)
                and all(prior_images.get(role) == self.old_images[role] for role in ('backend', 'frontend')),
                'prior app image receipt mismatch')
        require(prior_state.get('rehearsal', {}).get('ledger') == self.old_ledger, 'prior migration receipt mismatch')
        global OLD, OLD_COMMIT, OLD_LEDGER
        OLD = dict(self.old_images)
        OLD_COMMIT = self.old_commit
        OLD_LEDGER = self.old_ledger
        self.path = root / 'state.json'
        self.state = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'status': 'ready', 'completed': []}
        require(self.state.get('commit', commit) == commit, 'new release commit receipt mismatch')
        completed = self.state.get('completed', [])
        require(isinstance(completed, list) and completed == list(PHASES[:len(completed)]),
                'new release phase receipt invalid')
        self.state['commit'] = commit
        self.source = root / 'source'
        self.version = transition['target_version'] + '-ga401.' + commit[:8]
        self.tags = {role: 'multica-ga401-' + role + ':' + self.version for role in ('backend', 'frontend')}
        bindings = {'transition_sha256': transition_sha, 'source_tree': transition['source_tree'],
                    'source_archive_sha256': transition['source_archive_sha256'],
                    'deployment_tool_commit': transition['deployment_tool_commit'],
                    'deployment_tool_sha256': transition['deployment_tool_sha256']}
        progressed = bool(completed or self.state.get('running'))
        for key, expected in bindings.items():
            actual = self.state.get(key)
            require(actual == expected if actual is not None else not progressed,
                    'release input binding drift: ' + key)
            self.state.setdefault(key, expected)

    def save(self):
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.state, indent=2, sort_keys=True) + '\n')
        temporary.chmod(0o600)
        os.replace(temporary, self.path)

    def snapshot(self):
        return deployed_snapshot(self.commit, self.root)

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
        require(ledger() == OLD_LEDGER, 'unexpected deployed migration ledger')
        health(OLD_COMMIT)
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
        self.state['images'] = {role: inspect(tag, 'image')['Id'] for role, tag in self.tags.items()}
        # Runtime is a separately owned deployment and must not be rebuilt or
        # replaced by this app-only candidate. Bind its exact live image ID.
        self.state['images']['runtime'] = OLD['runtime']
        version = docker('run', '--rm', '--network', 'none', '--read-only', '--tmpfs', '/tmp',
                         '-e', 'HOME=/tmp/empty', '--entrypoint', '/app/multica',
                         self.tags['backend'], '--version').decode()
        require(self.version in version and self.commit[:8] in version, 'candidate CLI identity mismatch')

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

    def snapshot_uploads(self):
        backups = self.root / 'backups'
        backups.mkdir(mode=0o700, exist_ok=True)
        archive = backups / 'backend-uploads.tar'
        with archive.open('xb') as output:
            docker('run', '--rm', '--network', 'none', '--read-only',
                   '--mount', 'type=volume,src=multica_backend_uploads,dst=/data,readonly',
                   'alpine:3.21', 'sh', '-ec', 'tar -C /data -cpf - .', output=output)
        archive.chmod(0o600)
        require(archive.stat().st_size > 0, 'uploads archive is unexpectedly empty')
        archive_mount = 'type=bind,src=' + str(archive) + ',dst=/backup/backend-uploads.tar,readonly'
        restored = docker('run', '--rm', '--network', 'none', '--read-only',
                          '--tmpfs', '/restore:rw,size=8g',
                          '--mount', archive_mount,
                          'alpine:3.21', 'sh', '-ec',
                          'tar -C /restore -xpf /backup/backend-uploads.tar; find /restore -type f | wc -l; du -sk /restore').decode().splitlines()
        require(len(restored) == 2 and re.fullmatch(r'\d+', restored[0].strip())
                and re.fullmatch(r'\d+\s+/restore', restored[1].strip()),
                'uploads restore evidence malformed')
        return {'sha256': file_hash(archive), 'bytes': archive.stat().st_size,
                'restored_files': int(restored[0].strip()), 'restored_kib': int(restored[1].split()[0])}

    def rehearse(self):
        self.originals()
        dump = self.dump('rehearsal.dump')
        uploads = self.snapshot_uploads()
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
        before = migration_snapshot(self.source, True, **db)
        require(ledger(**db) == OLD_LEDGER, 'restored database has unexpected schema')
        docker('run', '--rm', '--network', network, '--read-only', '--entrypoint', '/app/migrate',
               '-e', 'DATABASE_URL=postgres://rehearsal@' + container + ':5432/rehearsal?sslmode=disable',
               self.tags['backend'], 'up')
        require(ledger(**db) == ledger_name(self.source), 'rehearsal migration incomplete')
        after = migration_snapshot(self.source, False, **db)
        validate_migration_snapshots(before, after)
        docker('stop', container)
        self.state['rehearsal'] = {'dump_sha256': file_hash(dump), 'dump_bytes': dump.stat().st_size,
                                   'uploads': uploads, 'ledger': ledger_name(self.source),
                                   'identities_sha256': digest(before['entity_ids'].encode()),
                                   'ledger_before': before['schema_history'],
                                   'ledger_after': after['schema_history'],
                                   'migration_delta': sorted(set(after['schema_history']) - set(before['schema_history'])),
                                   'migration_reconciliation_sha256': digest(json.dumps(
                                       {'before': before, 'after': after}, sort_keys=True).encode()),
                                   'migration_reconciliation': {'before': before, 'after': after}}

    def compose(self, runtime, *args):
        directory, filename, project = (RUNTIME, 'compose.yaml', 'multica-ga401-runtime') if runtime else (APP, 'docker-compose.selfhost.yml', 'multica')
        override = self.root / ('runtime-image.json' if runtime else 'app-images.json')
        return docker('compose', '--project-directory', str(directory), '-p', project, '-f', str(directory / filename),
                      '-f', str(override), *args)

    def activate(self):
        self.originals()
        idle()
        require(ledger() == OLD_LEDGER, 'production schema changed before cutover')
        for role, tag in self.tags.items():
            image = self.state['images'][role]
            require(inspect(tag, 'image')['Id'] == image, 'candidate tag moved')
        backup = self.root / 'backups'
        backup.mkdir(mode=0o700, exist_ok=True)
        if os.name != 'nt':
            require(backup.stat().st_mode & 0o077 == 0, 'backup directory permissions are not private')
        for prefix, directory, filename in [('app', APP, 'docker-compose.selfhost.yml'), ('runtime', RUNTIME, 'compose.yaml')]:
            for leaf in (filename, '.env'):
                source = directory / leaf
                if source.exists():
                    target = backup / (prefix + '-' + leaf)
                    shutil.copyfile(source, target)
                    target.chmod(0o600)
        (self.root / 'app-images.json').write_text(json.dumps({'services': {r: {'image': self.state['images'][r]}
                                                                         for r in ('backend', 'frontend')}}))
        # This release owns only the app Compose project. Never create an
        # override for, stop, restart or otherwise mutate the runtime project.
        before = compose_config(APP, 'docker-compose.selfhost.yml', 'multica')
        after = json.loads(self.compose(False, 'config', '--format', 'json'))
        for role in ('backend', 'frontend'):
            after['services'][role]['image'] = before['services'][role]['image']
        require(before == after, 'image override changes non-image configuration')
        idle()
        docker('stop', NAMES['frontend'], NAMES['backend'])
        self.state['writers_stopped'] = True
        self.save()
        require(sql("SELECT count(*) FROM agent_task_queue WHERE status NOT IN ('completed','failed','cancelled');") == '0',
                'new task arrived during quiescence; operator decision required')
        self.state['final_ids'] = identities()
        self.state['final_dump_sha256'] = file_hash(self.dump('final.dump'))
        for volume in ('multica_backend_uploads',):
            docker('run', '--rm', '--network', 'none', '--read-only', '--mount', 'type=volume,src=' + volume + ',dst=/data,readonly',
                   '--mount', 'type=bind,src=' + str(backup) + ',dst=/backup', 'alpine:3.21',
                   'sh', '-c', 'umask 077; tar -C /data -cpf /backup/' + volume + '.tar . && tar -tf /backup/' + volume + '.tar >/dev/null')
        self.save()
        self.compose(False, 'up', '-d', '--no-deps', '--no-build', '--pull', 'never', 'backend', 'frontend')
        self.wait_health()
        require(identities() == self.state['final_ids'], 'post-migration data identities changed')
        self.verify_containers(runtime=False)
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
        for role in ('backend', 'frontend'):
            current = inspect(NAMES[role])
            require(current['Image'] == self.state['images'][role] and current['State']['Running'], 'candidate not running')
            require(container_contract(current) == self.state['containers'][role]['contract'], 'container contract changed')
        # Runtime identity and contract belong to a separate deployment owner.
        # Verify them as unchanged without asking Compose to act on that project.
        current_runtime = inspect(NAMES['runtime'])
        require(current_runtime['State']['Running']
                and current_runtime['Id'] == self.state['containers']['runtime']['id']
                and current_runtime['Image'] == self.state['containers']['runtime']['image']
                and container_contract(current_runtime) == self.state['containers']['runtime']['contract'],
                'separate runtime changed during app upgrade')

    def verify(self):
        health(self.commit)
        require(ledger() == ledger_name(self.source), 'final migration ledger mismatch')
        self.verify_containers()
        env = dict(item.split('=', 1) for item in inspect(NAMES['backend'])['Config']['Env'])
        require(env.get('ALLOW_SIGNUP') == 'false', 'invite-only config changed')
        version = docker('exec', NAMES['backend'], '/app/multica', '--version').decode()
        require(self.version in version and self.commit[:8] in version, 'runtime CLI version mismatch')
        require(identities() == self.state['final_ids'], 'data identities changed during upgrade')
        for path in ('/', '/health', '/readyz'):
            body = http('https://agent.hankee.com' + path)
            if path == '/health':
                require(json.loads(body).get('commit') == self.commit, 'public route has wrong source')
        self.state['verified_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def deployed_snapshot(commit, root=None):
    """Read a completed deployment receipt without constructing a transition.

    This is deliberately read-only and binds the response to the live backend
    commit and the completed receipt for that same commit.
    """
    require(isinstance(commit, str) and HEX40.fullmatch(commit), 'exact source commit required')
    release_root = root or (BASE / commit)
    require(release_root == BASE / commit and release_root.resolve() == release_root, 'unexpected release root')
    path = release_root / 'state.json'
    require(path.is_file(), 'deployment receipt missing')
    state = json.loads(path.read_text(encoding='utf-8'))
    require(state.get('status') == 'complete' and state.get('completed') == list(PHASES), 'latest receipt is incomplete')
    require(state.get('commit') == commit, 'deployment receipt source mismatch')
    images = state.get('images')
    require(isinstance(images, dict) and set(images) == {'backend', 'frontend', 'runtime'}, 'deployment receipt images invalid')
    require(all(isinstance(v, str) and re.fullmatch(r'sha256:[0-9a-f]{64}', v) for v in images.values()), 'deployment receipt image ids invalid')
    config = state.get('config_hashes', [])
    require(isinstance(config, list) and all(isinstance(v, str) for v in config), 'deployment receipt config hashes invalid')
    ledger_value = state.get('rehearsal', {}).get('ledger')
    require(isinstance(ledger_value, str) and ledger_value, 'deployment receipt ledger missing')
    health(commit)
    require(ledger() == ledger_value, 'live migration ledger drift')
    require(config_hashes() == config, 'live compose configuration drift')
    for role, expected in images.items():
        current = inspect(NAMES[role])
        require(current['State']['Running'] and current['Image'] == expected, 'live deployment image drift')
        require(container_contract(current) == state['containers'][role]['contract'], 'live container contract drift')
    return {'source_commit': commit, 'images': dict(images), 'ledger': ledger_value, 'config_hashes': list(config)}


def ledger_name(source):
    return sorted(p.name.removesuffix('.up.sql') for p in (source / 'server/migrations').glob('*.up.sql'))[-1]


def main():
    import fcntl  # Remote-only; unit tests also run on Windows.
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=PHASES + ('snapshot',))
    parser.add_argument('--release-root', type=Path)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--transition-file', type=Path)
    parser.add_argument('--deployment-tool', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    with (BASE / '.upgrade.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.phase == 'snapshot':
            print(json.dumps(deployed_snapshot(args.source_commit, args.release_root), sort_keys=True))
        else:
            require(args.release_root is not None, '--release-root is required for upgrade phases')
            expected_tool = args.release_root / 'deployment-owner/upgrade.py'
            require(args.deployment_tool == expected_tool and Path(__file__).resolve() == expected_tool.resolve(),
                    'deployment tool must be the separately bound release runner')
            upgrade = Upgrade(args.release_root, args.source_commit, args.transition_file, args.deployment_tool)
            upgrade.phase(args.phase)


if __name__ == '__main__':
    main()
