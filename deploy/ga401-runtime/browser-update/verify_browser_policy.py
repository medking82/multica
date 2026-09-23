"""Exact deployment identities and mounts; never grant Docker authority to maintenance."""
import importlib.util
import json
from pathlib import Path
import sys

from browser_common import BASES, read_json, require

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('original_runtime_policy', HERE.parent / 'verify-runtime.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
RUNTIME = 'multica-ga401-runtime'
LOGIN = 'multica-ga401-browser'
IMAGES = {'runtime': RUNTIME + ':20260923-browser1', 'login': LOGIN + ':20260923-browser1'}
HOME = RUNTIME + '_runtime-home'
TOOLS = RUNTIME + '_cli-tools'
REPORTS = RUNTIME + '_cli-validation'
BROWSERS = RUNTIME + '_browser-tools'
PROFILE = LOGIN + '_browser-profile'
STATE = LOGIN + '_browser-state'
WATCH = ['python3', '/opt/runtime/browser-update/browser_watch.py']
ROLES = {
    'runtime': {'project': RUNTIME, 'service': 'runtime', 'image': 'runtime', 'uid': 1000,
        'cpus': 6, 'ram': 8, 'pids': 512, 'shm': 1024, 'tmp': '1g',
        'net': RUNTIME + '_default', 'restart': 'unless-stopped',
        'mounts': [(HOME, '/home/agent', True), (TOOLS, '/opt/agent-tools', False), (BROWSERS, '/opt/browser-tools', False)]},
    'updater': {'project': RUNTIME, 'service': 'updater', 'image': 'runtime', 'uid': 1001,
        'cpus': 1, 'ram': 1, 'pids': 64, 'shm': 64, 'tmp': '512m',
        'net': RUNTIME + '_cli-updates', 'restart': 'unless-stopped',
        'mounts': [(TOOLS, '/opt/agent-tools', True), (REPORTS, '/opt/validation-results', False), (BROWSERS, '/opt/browser-tools', True)]},
    'validator': {'project': RUNTIME, 'service': 'validator', 'image': 'runtime', 'uid': 1002,
        'cpus': 2, 'ram': 2, 'pids': 512, 'shm': 512, 'tmp': '1g',
        'net': 'none', 'restart': 'unless-stopped',
        'mounts': [(TOOLS, '/opt/agent-tools', False), (REPORTS, '/opt/validation-results', True), (BROWSERS, '/opt/browser-tools', False)]},
    'login': {'project': LOGIN, 'service': 'login', 'image': 'login', 'uid': 1000,
        'cpus': 2, 'ram': 3, 'pids': 1024, 'shm': 512, 'tmp': '512m',
        'net': LOGIN + '_default', 'restart': 'no',
        'mounts': [(PROFILE, '/home/browser', True), (STATE, '/opt/browser-state', True), (BROWSERS, '/opt/browser-tools', False)]},
    'login-validator': {'project': LOGIN, 'service': 'validator', 'image': 'login', 'uid': 1002,
        'cpus': 2, 'ram': 2, 'pids': 512, 'shm': 512, 'tmp': '1g',
        'net': 'none', 'restart': 'unless-stopped',
        'mounts': [(BROWSERS, '/opt/browser-tools', False), (REPORTS, '/opt/validation-results', True)]},
}


def roles_for(project):
    require(project in (RUNTIME, LOGIN), 'Unapproved Compose project')
    return {k: v for k, v in ROLES.items() if v['project'] == project}


def role_for(project, service):
    matches = [(k, v) for k, v in roles_for(project).items() if v['service'] == service]
    require(len(matches) == 1, 'Unapproved service')
    return matches[0]


def check_config(config):
    base.pinned_seccomp()
    project = config.get('name')
    roles = roles_for(project)
    require(set(config.get('services', {})) == {v['service'] for v in roles.values()}, 'Unexpected service set')
    expected_volumes = {v for item in roles.values() for v, _, _ in item['mounts']}
    volumes = config.get('volumes', {})
    require({v.get('name') for v in volumes.values()} == expected_volumes and len(volumes) == len(expected_volumes),
            'Unexpected volume declarations')
    for value in volumes.values():
        require(not value.get('driver_opts') and not value.get('driver'), 'No host-backed/custom volume drivers')
        external = value['name'] != BROWSERS or project == LOGIN
        require(bool(value.get('external')) == external, 'Existing volumes must be explicitly retained')
    expected_networks = {item['net'] for item in roles.values() if item['net'] != 'none'}
    networks = config.get('networks', {})
    require({v.get('name') for v in networks.values()} == expected_networks, 'Unexpected network declarations')
    for value in networks.values():
        require(value.get('external') is True and not value.get('driver_opts')
                and value.get('driver', 'bridge') == 'bridge', 'Private existing bridges only')
    for service_name, service in config['services'].items():
        role, wanted = role_for(project, service_name)
        require(service.get('image') == IMAGES[wanted['image']] and service.get('pull_policy') == 'never'
                and not service.get('build'), 'Only verified local images may deploy')
        require(service.get('user') == f"{wanted['uid']}:{wanted['uid']}" and
                service.get('read_only') is True and service.get('init') is True
                and service.get('cap_drop') == ['ALL'], 'Non-root readonly dropped-capability boundary')
        for key in ('cap_add', 'privileged', 'ports', 'devices', 'group_add', 'secrets', 'gpus',
                    'device_cgroup_rules', 'volumes_from', 'pid', 'ipc', 'env_file', 'extra_hosts',
                    'configs', 'post_start', 'pre_stop', 'develop'):
            require(not service.get(key), 'Unapproved service authority: ' + key)
        security = service.get('security_opt', [])
        require(len(security) == 2 and 'no-new-privileges:true' in security and
                any(x.startswith('seccomp=') and x.endswith('seccomp-profile.json') for x in security),
                'Canonical seccomp/NNP required')
        require(float(service.get('cpus', 0)) == wanted['cpus'] and service.get('pids_limit') == wanted['pids']
                and int(service.get('mem_limit', 0)) == wanted['ram'] * 1024**3
                and int(service.get('memswap_limit', 0)) == wanted['ram'] * 1024**3
                and int(service.get('shm_size', 64 * 1024**2)) == wanted['shm'] * 1024**2,
                'Browser resource bounds changed')
        require(service.get('tmpfs') == ['/tmp:rw,nosuid,nodev,size=' + wanted['tmp'] + ',mode=1777'], 'Private bounded tmpfs required')
        require(service.get('restart') == wanted['restart'], 'Restart policy changed')
        mappings = service.get('volumes', [])
        require(all(v.get('type') == 'volume' for v in mappings), 'Bind mounts are forbidden')
        actual = [(volumes[v['source']]['name'], v['target'], not v.get('read_only', False)) for v in mappings]
        require(sorted(actual) == sorted(wanted['mounts']), 'Wrong volume identity/access')
        if wanted['net'] == 'none':
            require(service.get('network_mode') == 'none' and not service.get('networks'), 'Validation must be offline')
        else:
            require(not service.get('network_mode') and
                    {networks[k]['name'] for k in service.get('networks', {})} == {wanted['net']}, 'Wrong bridge attachment')
        if role in ('runtime', 'login'):
            require(not service.get('entrypoint') and not service.get('command'), 'Consumer entrypoint is image-owned')
        else:
            require(service.get('entrypoint') == WATCH and service.get('command') == [role, 'watch'], 'Wrong maintenance role')
        expected_env = {'MULTICA_DAEMON_DEVICE_NAME': 'GA401-Isolated', 'MULTICA_DAEMON_MAX_CONCURRENT_TASKS': '2'} if role == 'runtime' else {}
        require((service.get('environment') or {}) == expected_env, 'Unexpected Compose environment')


def check_inspect(items, *, include_login=True):
    verified = read_json(HERE / 'verified-images.json')
    expected_roles = set(ROLES) if include_login else set(ROLES) - {'login'}
    found = set()
    for item in items:
        config, host = item['Config'], item['HostConfig']
        labels = config.get('Labels', {})
        role, wanted = role_for(labels.get('com.docker.compose.project'), labels.get('com.docker.compose.service'))
        require(role in expected_roles, 'Unexpected role for this deployment phase')
        require(role not in found, 'Duplicate live role')
        found.add(role)
        image = verified[wanted['image']]
        require(item.get('Image') == image['id'] and config.get('Image') == IMAGES[wanted['image']], 'Wrong live image digest')
        require(config.get('User') == f"{wanted['uid']}:{wanted['uid']}" and
                host.get('ReadonlyRootfs') and host.get('CapDrop') == ['ALL'] and host.get('Init') is True,
                'Live sandbox boundary changed')
        require(labels.get('io.hankee.owner') == wanted['project'], 'Wrong live owner')
        for key in ('Privileged', 'CapAdd', 'PidMode', 'PortBindings', 'Devices', 'GroupAdd',
                    'VolumesFrom', 'ExtraHosts', 'DeviceRequests'):
            require(not host.get(key), 'Unexpected live authority: ' + key)
        require(host.get('IpcMode') == 'private', 'Host IPC forbidden')
        require(host.get('RestartPolicy', {}).get('Name') == wanted['restart'], 'Wrong live restart policy')
        require(host.get('Memory') == wanted['ram'] * 1024**3 and host.get('MemorySwap') == wanted['ram'] * 1024**3
                and host.get('NanoCpus') == wanted['cpus'] * 10**9 and host.get('PidsLimit') == wanted['pids']
                and host.get('ShmSize') == wanted['shm'] * 1024**2, 'Live resource bounds changed')
        require(host.get('Tmpfs') == {'/tmp': 'rw,nosuid,nodev,size=' + wanted['tmp'] + ',mode=1777'}, 'Wrong live tmpfs')
        security = host.get('SecurityOpt', [])
        require(len(security) == 2 and any(x in security for x in ('no-new-privileges', 'no-new-privileges:true')),
                'Live no-new-privileges required')
        profiles = [json.loads(x[8:]) for x in security if x.startswith('seccomp=')]
        require(profiles == [base.pinned_seccomp()], 'Live seccomp differs')
        mounts = item.get('Mounts', [])
        require(all(v.get('Type') == 'volume' for v in mounts), 'Live host mount forbidden')
        require(sorted((v.get('Name'), v.get('Destination'), v.get('RW')) for v in mounts) == sorted(wanted['mounts']),
                'Live volume identity/access mismatch')
        binds = sorted(v + ':' + target + (':rw' if rw else ':ro') for v, target, rw in wanted['mounts'])
        require(not host.get('Binds') or sorted(host['Binds']) == binds, 'Unexpected legacy bind authority')
        require(host.get('NetworkMode') == wanted['net'] and
                set(item['NetworkSettings'].get('Networks', {})) == {wanted['net']}, 'Wrong live network')
        env = dict(x.split('=', 1) for x in image['env'])
        if role == 'runtime':
            env.update(MULTICA_DAEMON_DEVICE_NAME='GA401-Isolated', MULTICA_DAEMON_MAX_CONCURRENT_TASKS='2')
        require(dict(x.split('=', 1) for x in config['Env']) == env, 'Live environment differs from verified image')
        entrypoint = image['entrypoint'] if role in ('runtime', 'login') else WATCH
        command = image['command'] if role in ('runtime', 'login') else [role, 'watch']
        require(config.get('Entrypoint') == entrypoint and config.get('Cmd') == command, 'Wrong live launch command')
    require(found == expected_roles, 'All expected live roles must be verified')


def check_inheritance(items):
    require(len(items) == 4, 'Expected two parent/candidate image pairs')
    result = {}
    for consumer, before, after in [('runtime', items[0], items[1]), ('login', items[2], items[3])]:
        require(before['Id'] == BASES[consumer], 'Parent image identity changed')
        layers = before['RootFS']['Layers']
        require(after['RootFS']['Layers'][:len(layers)] == layers, 'Parent filesystem changed')
        old_env = dict(x.split('=', 1) for x in before['Config']['Env'])
        old_env.update(NODE_PATH='/opt/browser-tools/current/node_modules:/usr/local/lib/node_modules',
                       PLAYWRIGHT_BROWSERS_PATH='0', PLAYWRIGHT_SKIP_BROWSER_GC='1')
        require(dict(x.split('=', 1) for x in after['Config']['Env']) == old_env, 'Unapproved image environment change')
        expected_entrypoint = before['Config']['Entrypoint'] if consumer == 'runtime' else [
            'python3', '/opt/runtime/browser-update/browser_launcher.py', 'login']
        require(after['Config']['Entrypoint'] == expected_entrypoint
                and after['Config'].get('Cmd') == before['Config'].get('Cmd')
                and after['Config']['User'] == before['Config']['User'] == '1000:1000', 'Image entrypoint/identity drift')
        result[consumer] = {'id': after['Id'], 'env': after['Config']['Env'],
                            'entrypoint': expected_entrypoint, 'command': after['Config'].get('Cmd')}
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        action = sys.argv[1]
        {'config': check_config, 'inspect': check_inspect,
         'inspect-runtime': lambda items: check_inspect(items, include_login=False),
         'inheritance': check_inheritance}[action](json.load(sys.stdin))
        print(action + ' browser update policy PASS')
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        raise SystemExit('Browser update policy FAIL: ' + str(error))
