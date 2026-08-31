import copy
import unittest

import verify_browser_policy as policy


def fixture(project):
    roles = policy.roles_for(project)
    volume_names = {v for item in roles.values() for v, _, _ in item['mounts']}
    volumes = {name: {'name': name, 'external': name != policy.BROWSERS or project == policy.LOGIN}
               for name in volume_names}
    networks = {item['net']: {'name': item['net'], 'external': True}
                for item in roles.values() if item['net'] != 'none'}
    services = {}
    for role, item in roles.items():
        service = {'image': policy.IMAGES[item['image']], 'pull_policy': 'never',
            'user': f"{item['uid']}:{item['uid']}", 'read_only': True, 'init': True,
            'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true', 'seccomp=../seccomp-profile.json'],
            'cpus': item['cpus'], 'mem_limit': item['ram'] * 1024**3,
            'memswap_limit': item['ram'] * 1024**3, 'pids_limit': item['pids'],
            'shm_size': item['shm'] * 1024**2, 'restart': item['restart'],
            'tmpfs': ['/tmp:rw,nosuid,nodev,size=' + item['tmp'] + ',mode=1777'],
            'volumes': [{'type': 'volume', 'source': name, 'target': target, 'read_only': not rw}
                        for name, target, rw in item['mounts']]}
        if item['net'] == 'none':
            service['network_mode'] = 'none'
        else:
            service['networks'] = {item['net']: {}}
        if role == 'runtime':
            service['environment'] = {'MULTICA_DAEMON_DEVICE_NAME': 'GA401-Isolated',
                                      'MULTICA_DAEMON_MAX_CONCURRENT_TASKS': '2'}
        if role not in ('runtime', 'login'):
            service.update(entrypoint=policy.WATCH, command=[role, 'watch'])
        services[item['service']] = service
    return {'name': project, 'services': services, 'volumes': volumes, 'networks': networks}


class PolicyTests(unittest.TestCase):
    def test_human_browser_has_bounded_thread_headroom(self):
        # Linux pids.max counts threads as well as processes. Real login profiles
        # exhausted 512 while tabs and extensions were loading.
        self.assertEqual(policy.ROLES['login']['pids'], 1024)
        self.assertEqual({role: item['pids'] for role, item in policy.ROLES.items()
                          if role != 'login'},
                         {'runtime': 512, 'updater': 64, 'validator': 512,
                          'login-validator': 512})
        config = fixture(policy.LOGIN)
        config['services']['login']['pids_limit'] = 1024
        policy.check_config(config)
        for limit in (512, -1, 0, 2048):
            config['services']['login']['pids_limit'] = limit
            with self.subTest(limit=limit), self.assertRaisesRegex(ValueError, 'resource'):
                policy.check_config(config)

    def test_exact_two_project_configs(self):
        policy.check_config(fixture(policy.RUNTIME))
        policy.check_config(fixture(policy.LOGIN))

    def test_no_new_docker_host_or_credential_authority(self):
        for project in (policy.RUNTIME, policy.LOGIN):
            original = fixture(project)
            service = 'runtime' if project == policy.RUNTIME else 'login'
            for key, value in [('privileged', True), ('cap_add', ['SYS_ADMIN']),
                               ('ports', ['9222:9222']), ('pid', 'host'),
                               ('environment', {'OPENAI_API_KEY': 'fixture'}),
                               ('env_file', ['private.env']), ('gpus', 'all'),
                               ('post_start', [{'command': 'unapproved'}]),
                               ('user', '0:0'), ('read_only', False)]:
                modified = copy.deepcopy(original)
                modified['services'][service][key] = value
                with self.subTest(project=project, key=key), self.assertRaises(ValueError):
                    policy.check_config(modified)

    def test_private_profile_not_granted_to_agents_or_validation(self):
        for project in (policy.RUNTIME, policy.LOGIN):
            for service in ('validator',):
                config = fixture(project)
                config['services'][service]['volumes'].append({
                    'type': 'bind', 'source': '/private', 'target': '/home/browser', 'read_only': True})
                with self.assertRaisesRegex(ValueError, 'Bind'):
                    policy.check_config(config)

    def test_read_only_artifact_and_report_boundaries(self):
        for service, target in [('runtime', '/opt/browser-tools'), ('updater', '/opt/validation-results'),
                                ('validator', '/opt/browser-tools')]:
            config = fixture(policy.RUNTIME)
            volume = next(v for v in config['services'][service]['volumes'] if v['target'] == target)
            volume['read_only'] = False
            with self.subTest(service=service), self.assertRaisesRegex(ValueError, 'volume'):
                policy.check_config(config)

    def test_both_validators_must_be_offline(self):
        for project in (policy.RUNTIME, policy.LOGIN):
            config = fixture(project)
            config['services']['validator']['network_mode'] = 'host'
            with self.assertRaisesRegex(ValueError, 'offline'):
                policy.check_config(config)

    def test_canonical_seccomp_and_resources_preserved(self):
        for key, value in [('security_opt', ['seccomp=unconfined']), ('mem_limit', 0),
                           ('pids_limit', -1), ('tmpfs', ['/tmp:rw,exec'])]:
            config = fixture(policy.RUNTIME)
            config['services']['validator'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                policy.check_config(config)

    def test_existing_named_volumes_cannot_silently_be_recreated(self):
        config = fixture(policy.RUNTIME)
        config['volumes'][policy.HOME]['external'] = False
        with self.assertRaisesRegex(ValueError, 'retained'):
            policy.check_config(config)

    def test_partial_deployment_still_requires_all_runtime_and_maintenance_roles(self):
        for include_login in (True, False):
            with self.subTest(include_login=include_login), self.assertRaisesRegex(ValueError, 'All expected'):
                policy.check_inspect([], include_login=include_login)


if __name__ == '__main__':
    unittest.main()
