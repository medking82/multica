"""Reject unexpected Docker authority before deployment and after container creation."""
import copy
import importlib.util
import json
from pathlib import Path
import sys

from common import BASE_ID, require

spec = importlib.util.spec_from_file_location('base_policy', Path(__file__).parent.parent / 'verify-runtime.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
PROJECT = base.PROJECT
IMAGE = PROJECT + ':20260831-hot2'
IMAGE_ID = 'sha256:d69f3f5f17ae3c19f731e6a8dd0c915497c93629920b32716f9a8cb15b2e3082'
SIDECARS = {'updater': (1001, 1, 64), 'validator': (1002, 2, 128)}
MOUNTS = {
    'runtime': [('runtime-home', '/home/agent', False), ('cli-tools', '/opt/agent-tools', True)],
    'updater': [('cli-tools', '/opt/agent-tools', False), ('cli-validation', '/opt/validation-results', True)],
    'validator': [('cli-tools', '/opt/agent-tools', True), ('cli-validation', '/opt/validation-results', False)],
}


def check_config(config):
    base.pinned_seccomp()
    require(config.get('name') == PROJECT and set(config.get('services', {})) == set(MOUNTS), 'Unexpected services/project')
    require(set(config.get('volumes', {})) == {'runtime-home', 'cli-tools', 'cli-validation'}, 'Unexpected volume declarations')
    for name, volume in config['volumes'].items():
        require(volume.get('name') == PROJECT + '_' + name and not volume.get('external')
                and not volume.get('driver_opts'), 'Volumes must be private and project-owned')
    require(set(config.get('networks', {})) == {'default', 'cli-updates'}, 'Unexpected networks')
    for name, network in config['networks'].items():
        require(network.get('name') == PROJECT + '_' + name and not network.get('external')
                and not network.get('driver_opts') and network.get('driver', 'bridge') == 'bridge', 'Unsafe network')
    for name, service in config['services'].items():
        require(service.get('image') == IMAGE and service.get('pull_policy') == 'never' and not service.get('build'),
                'Deployment must use the verified local image')
        require(all(x.get('type') == 'volume' and isinstance(x.get('source'), str)
                    and isinstance(x.get('target'), str) for x in service.get('volumes', [])), 'Unsafe mount types')
        mounts = sorted((x.get('source'), x.get('target'), x.get('read_only', False)) for x in service.get('volumes', []))
        require(mounts == sorted(MOUNTS[name]) and all(x.get('type') == 'volume' for x in service['volumes']), 'Unsafe mounts')
        require(service.get('restart') == 'unless-stopped', 'Restart policy changed')
        require(service.get('read_only') is True and service.get('cap_drop') == ['ALL']
                and service.get('init') is True, 'Read-only root, init and dropped capabilities required')
        for key in ('cap_add', 'privileged', 'ports', 'devices', 'group_add', 'secrets', 'device_cgroup_rules',
                    'volumes_from', 'pid', 'ipc', 'env_file', 'extra_hosts', 'configs'):
            require(not service.get(key), 'Forbidden service option: ' + key)
        security = service.get('security_opt', [])
        require(len(security) == 2 and 'no-new-privileges:true' in security and
                any(x.startswith('seccomp=') and x.endswith('seccomp-profile.json') for x in security), 'Sandbox policy changed')
        if name == 'runtime':
            require(service.get('stop_grace_period') in (120, '120s', '2m', '2m0s'), 'Runtime drain grace changed')
            continue
        uid, cpus, pids = SIDECARS[name]
        require(service.get('user') == f'{uid}:{uid}', 'Wrong maintenance UID')
        require(service.get('entrypoint') == ['python3', f'/opt/runtime/hot-update/{name}.py']
                and service.get('command') == ['watch'], 'Wrong maintenance entrypoint')
        require(not service.get('environment'), 'Maintenance must not receive account environment')
        require(float(service.get('cpus', 0)) == cpus and service.get('pids_limit') == pids
                and int(service.get('mem_limit', 0)) == 1024**3
                and int(service.get('memswap_limit', 0)) == 1024**3, 'Maintenance resource limits changed')
        if name == 'validator':
            require(service.get('network_mode') == 'none' and not service.get('networks'), 'Validation must be offline')
        else:
            require(not service.get('network_mode') and set(service.get('networks', {})) == {'cli-updates'}, 'Updater network changed')
    # Reuse, rather than weaken, the previously reviewed runtime policy.
    original = copy.deepcopy(config)
    original['services'] = {'runtime': original['services']['runtime']}
    original['services']['runtime']['image'] = base.IMAGE
    original['services']['runtime']['volumes'] = [x for x in original['services']['runtime']['volumes'] if x['source'] == 'runtime-home']
    original['volumes'] = {'runtime-home': original['volumes']['runtime-home']}
    original['networks'] = {'default': original['networks']['default']}
    base.check_config(original)


def check_inspect(items):
    require(len(items) == 3, 'All three containers must be inspected')
    roles = {x['Config']['Labels'].get('com.docker.compose.service'): x for x in items}
    require(set(roles) == set(MOUNTS), 'Wrong live service identities')
    for name, item in roles.items():
        config, host = item['Config'], item['HostConfig']
        require(item.get('Image') == IMAGE_ID and config.get('Image') == IMAGE, 'Live image differs from verified digest')
        require(config.get('Labels', {}).get('io.hankee.owner') == PROJECT, 'Wrong owner')
        require(host.get('ReadonlyRootfs') and host.get('CapDrop') == ['ALL']
                and not host.get('CapAdd') and host.get('Init') is True, 'Live sandbox boundary changed')
        require(host.get('RestartPolicy', {}).get('Name') == 'unless-stopped', 'Restart not enabled')
        for key in ('Privileged', 'PidMode', 'PortBindings', 'Devices', 'GroupAdd', 'VolumesFrom'):
            require(not host.get(key), 'Forbidden live authority: ' + key)
        security = host.get('SecurityOpt', [])
        require(len(security) == 2 and any(x in security for x in ('no-new-privileges', 'no-new-privileges:true')), 'Live NNP missing')
        seccomp = [x.removeprefix('seccomp=') for x in security if x.startswith('seccomp=')]
        require(len(seccomp) == 1 and json.loads(seccomp[0]) == base.pinned_seccomp(), 'Live seccomp differs')
        require(all(x.get('Type') == 'volume' and isinstance(x.get('Name'), str)
                    and isinstance(x.get('Destination'), str) for x in item.get('Mounts', [])), 'Foreign live mount type')
        mounts = sorted((x.get('Name'), x.get('Destination'), not x.get('RW')) for x in item.get('Mounts', []))
        expected = sorted((PROJECT + '_' + v, path, ro) for v, path, ro in MOUNTS[name])
        require(mounts == expected and all(x.get('Type') == 'volume' for x in item['Mounts']), 'Live volume authority mismatch')
        binds = sorted(PROJECT + '_' + v + ':' + path + (':ro' if ro else ':rw') for v, path, ro in MOUNTS[name])
        require(not host.get('Binds') or sorted(host['Binds']) == binds, 'Foreign legacy mounts')
        forbidden = {'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_API_KEY', 'GH_TOKEN', 'GITHUB_TOKEN'}
        require(not forbidden.intersection(x.split('=', 1)[0] for x in config.get('Env', [])), 'Unexpected credential environment')
        if name == 'runtime':
            original = copy.deepcopy(item)
            original['Image'] = base.IMAGE_ID
            original['Config']['Image'] = base.IMAGE
            original['HostConfig']['Binds'] = [base.VOLUME + ':/home/agent:rw']
            original['Mounts'] = [x for x in original['Mounts'] if x['Destination'] == '/home/agent']
            base.check_inspect([original])
        else:
            uid, cpus, pids = SIDECARS[name]
            require(config.get('User') == f'{uid}:{uid}' and config.get('Entrypoint') ==
                    ['python3', f'/opt/runtime/hot-update/{name}.py'] and config.get('Cmd') == ['watch'], 'Live maintenance launch changed')
            require(host.get('Memory') == 1024**3 and host.get('MemorySwap') == 1024**3
                    and host.get('NanoCpus') == cpus * 10**9 and host.get('PidsLimit') == pids, 'Live maintenance resource bounds')
            network = 'none' if name == 'validator' else PROJECT + '_cli-updates'
            require(host.get('NetworkMode') == network and
                    set(item.get('NetworkSettings', {}).get('Networks', {})) == {network}, 'Live maintenance network changed')


def check_inheritance(items):
    require(len(items) == 2 and items[0]['Id'] == BASE_ID, 'Build parent is not the reviewed image')
    old, new = items
    count = len(old['RootFS']['Layers'])
    require(new['RootFS']['Layers'][:count] == old['RootFS']['Layers'], 'Parent layers changed')
    for key in ('Entrypoint', 'Cmd', 'User', 'Env'):
        require(old['Config'].get(key) == new['Config'].get(key), 'Inherited runtime config changed: ' + key)
    print(new['Id'])


if __name__ == '__main__':
    try:
        action = sys.argv[1]
        {'config': check_config, 'inspect': check_inspect, 'inheritance': check_inheritance}[action](json.load(sys.stdin))
        print(action + ' hot-update isolation PASS')
    except (ValueError, KeyError, TypeError, IndexError) as error:
        raise SystemExit('Hot-update isolation FAIL: ' + str(error))
