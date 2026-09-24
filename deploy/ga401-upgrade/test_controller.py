import hashlib, importlib.util, json, os, subprocess, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).parent
spec=importlib.util.spec_from_file_location("controller", ROOT/"controller.py"); controller=importlib.util.module_from_spec(spec); spec.loader.exec_module(controller)

class P:
    def __init__(self, out="ok", code=0): self.stdout=out; self.returncode=code

class Tests(unittest.TestCase):
    def template(self, repo, **extra):
        value = {'prior_commit': 'b'*40, 'prior_ledger': '450_test',
                 'prior_images': {}, 'target_version': '0.5.2', 'upstream_commit': 'c'*40, **extra}
        path = repo / 'deploy/ga401-upgrade/transition.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def test_readiness_runs_diff_check_and_remote_probe(self):
        calls=[]
        def ex(argv, **kw): calls.append(argv); return P()
        controller.readiness(Path("."), ex)
        self.assertEqual(calls[0][:3], ["git","diff","--check"]); self.assertEqual(calls[2][0], "ssh")

    def test_deploy_refuses_missing_or_drifted_commit_before_archive(self):
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d); (repo/".git").mkdir()
            def ex(argv, **kw):
                if argv[0]=="git" and argv[1:]==["rev-parse","HEAD"]: return P("a"*40+"\n")
                if argv[0]=="git" and argv[1:]==["rev-parse","--git-common-dir"]: return P(str(repo/".git")+"\n")
                return P()
            with self.assertRaises(controller.Failure): controller.deploy(repo, repo/"state.json", {}, ex)
            self.assertFalse((repo/".git/sop/ga401-upgrade").exists())

    def test_deploy_binds_committed_runner_and_separate_product_source(self):
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d); (repo/".git").mkdir()
            self.template(repo, source_commit='b'*40, source_tree='d'*40)
            (repo / controller.SCRIPT).write_bytes(b'checkout with different line endings\r\n')
            calls=[]; uploaded={}; committed_runner=b'committed runner\n'
            def ex(argv, **kw):
                calls.append(argv)
                if argv[0]=="git" and argv[1:]==["rev-parse","HEAD"]: return P("a"*40)
                if argv[0]=="git" and argv[1:]==["rev-parse","--git-common-dir"]: return P(str(repo/".git"))
                if argv[0]=='git' and argv[1] == 'rev-parse': return P('d'*40)
                if argv[0]=='git' and argv[1] == 'status': return P('')
                if argv[0]=='scp': uploaded[argv[-1].removeprefix('ga401:')] = Path(argv[-2]).read_bytes(); return P('')
                if argv[0]=='ssh' and argv[-1].startswith('sha256sum '):
                    target = argv[-1].split(' ', 1)[1]
                    return P(hashlib.sha256(uploaded[target]).hexdigest() + '  input')
                return P('{}')
            def popen(argv, **kw):
                calls.append(argv)
                class Child:
                    returncode = 0
                    def communicate(self):
                        kw['stdout'].write(committed_runner if argv[1] == 'cat-file' else b'product archive')
                        return None, b''
                return Child()
            env={"SOP_RELEASE_COMMIT":"a"*40}
            with patch.object(controller, 'product_repo', return_value=repo), patch.object(controller.subprocess, 'Popen', side_effect=popen):
                result = controller.deploy(repo, repo/'state.json', env, ex)
            remote = controller.REMOTE + '/' + 'b'*40
            self.assertEqual(uploaded[remote+'/deployment-owner/upgrade.py'], committed_runner)
            transition = json.loads(uploaded[remote+'/transition.json'])
            self.assertEqual(transition['deployment_tool_commit'], 'a'*40)
            self.assertEqual(transition['source_commit'], 'b'*40)
            self.assertEqual(transition['deployment_tool_sha256'], hashlib.sha256(committed_runner).hexdigest())
            self.assertEqual(result['commit'], 'a'*40)
            self.assertEqual(result['source_commit'], 'b'*40)
            extract = next(c[-1] for c in calls if c[0] == 'ssh' and 'tarfile.open' in c[-1])
            self.assertIn("filter=", extract)
            self.assertIn('issym()', extract)

    def test_full_uses_declared_portable_go_and_fixture_database(self):
        calls=[]; envs=[]
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/".git"/"sop").mkdir(parents=True)
            self.template(root)
            (root/"scripts"/"custom-desktop").mkdir(parents=True)
            (root/"scripts"/"custom-desktop"/"check.mjs").write_text("")
            def ex(argv, **kw):
                calls.append(argv); envs.append(kw.get("env", {}))
                if argv[0] == "git": return P(str(root/".git"))
                return P('\n'.join(json.dumps({'Action': 'pass', 'Test': name}) for name in controller.AUTH_TESTS))
            with patch.object(controller, 'GO', root / 'go.exe'), \
                 patch.object(controller, 'NODE', root / 'node.exe'), \
                 patch.object(controller, 'PNPM', root / 'pnpm.mjs'), \
                 patch.object(controller, 'PNPM_SHIM', root / 'pnpm.cmd'):
                for tool in (controller.GO, controller.NODE, controller.PNPM, controller.PNPM_SHIM):
                    tool.touch()
                controller.full(root, ex)
        self.assertTrue(calls)
        go_calls=[(c,e) for c,e in zip(calls,envs) if str(c[0]).endswith("go.exe")]
        self.assertTrue(go_calls)
        self.assertIn("127.0.0.1:13312", go_calls[0][1]["DATABASE_URL"])

    def test_pinned_source_refuses_wrong_head_tree_or_dirty_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.template(root, source_commit='a'*40, source_tree='d'*40)
            for head, tree, status in [('f'*40, 'd'*40, ''), ('a'*40, 'f'*40, ''), ('a'*40, 'd'*40, ' M server/auth.go')]:
                def ex(argv, **kw):
                    if argv[1:] == ['rev-parse', 'HEAD']: return P(head)
                    if argv[1:] == ['rev-parse', 'HEAD^{tree}']: return P(tree)
                    if argv[1] == 'status': return P(status)
                    self.fail('unexpected command')
                with patch.dict(os.environ, {'MULTICA_GA401_PRODUCT_REPO': str(root)}):
                    with self.assertRaises(controller.Failure): controller.product_repo(root, ex)

    def test_pinned_source_requires_explicit_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.template(root, source_commit='a'*40, source_tree='d'*40)
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(controller.Failure, 'PRODUCT_REPO'):
                    controller.product_repo(root)

    def test_full_requires_every_expected_auth_suite_and_no_skips(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.template(root)
            for events in [[], [{'Action': 'pass', 'Test': 'TestSignupGating'}],
                           [{'Action': 'pass', 'Test': n} for n in controller.AUTH_TESTS] + [{'Action': 'skip', 'Test': 'TestSignupGating/closed'}]]:
                with patch.object(controller, 'custom_gate'), patch.object(controller, 'test_env', return_value={}), \
                     patch.object(controller, 'run', return_value='\n'.join(map(json.dumps, events))):
                    with self.assertRaisesRegex(controller.Failure, 'without skips'):
                        controller.full(root)

    def test_phase_command_uses_separate_owner_and_external_transition(self):
        source = 'a'*40
        root = controller.REMOTE + '/' + source
        command = controller.phase_command(root, source, 'rehearse')
        self.assertIn('/deployment-owner/upgrade.py rehearse', command)
        self.assertIn('--transition-file ' + root + '/transition.json', command)
        self.assertIn('--deployment-tool ' + root + '/deployment-owner/upgrade.py', command)
        self.assertNotIn('/source/deploy/', command)
        with self.assertRaises(controller.Failure): controller.phase_command(root, source, 'destroy')
        with self.assertRaises(controller.Failure): controller.phase_command(root, 'a;bad', 'activate')

    def test_verify_rejects_source_or_input_drift_before_phase(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            state_path = root / 'sop/ga401-upgrade-state.json'
            state_path.parent.mkdir()
            state = {'commit': 'a'*40, 'source_commit': 'b'*40, 'remote_root': controller.REMOTE + '/' + 'b'*40,
                     'deployment_tool_sha256': 'c'*64, 'transition_sha256': 'd'*64, 'archive_sha256': 'e'*64}
            state_path.write_text(json.dumps(state))
            with patch.object(controller, '_commit', return_value='a'*40), patch.object(controller, 'common_dir', return_value=root), \
                 patch.object(controller, 'ssh', return_value='f'*64 + '  runner.py') as remote:
                with self.assertRaisesRegex(controller.Failure, 'input drift'): controller.verify(root)
                self.assertEqual(remote.call_count, 1)

    def test_missing_toolchain_reports_clear_error(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(controller, 'GO', Path(d) / 'missing-go.exe'):
                with self.assertRaisesRegex(controller.Failure, 'toolchain missing'):
                    controller.test_env()

    def test_missing_custom_gate_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(controller.Failure, "missing"):
                controller.custom_gate(Path(d), "quick", lambda *a, **k: P())

if __name__=="__main__": unittest.main()
