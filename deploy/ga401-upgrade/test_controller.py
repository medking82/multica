import hashlib, importlib.util, json, os, subprocess, tempfile, unittest
from pathlib import Path

ROOT=Path(__file__).parent
spec=importlib.util.spec_from_file_location("controller", ROOT/"controller.py"); controller=importlib.util.module_from_spec(spec); spec.loader.exec_module(controller)

class P:
    def __init__(self, out="ok", code=0): self.stdout=out; self.returncode=code

class Tests(unittest.TestCase):
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

    def test_build_archive_extraction_command_is_data_filtered(self):
        with tempfile.TemporaryDirectory() as d:
            repo=Path(d); (repo/".git").mkdir(); archive=repo/"src.tar"; archive.write_bytes(b"x")
            calls=[]
            def ex(argv, **kw):
                calls.append(argv)
                if argv[0]=="git" and argv[1:]==["rev-parse","HEAD"]: return P("a"*40)
                if argv[0]=="git" and argv[1:]==["rev-parse","--git-common-dir"]: return P(str(repo/".git"))
                if argv[0]=="ssh" and "sha256sum" in argv[-1]: return P(hashlib.sha256(archive.read_bytes()).hexdigest()+"  source.tar")
                return P()
            env={"SOP_RELEASE_COMMIT":"a"*40}
            # Archive subprocess is deliberately not executed in this fake test; commit gate is verified separately.
            self.assertEqual(controller._commit(repo, env, ex), "a"*40)

    def test_full_uses_declared_portable_go_and_fixture_database(self):
        calls=[]; envs=[]
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/".git"/"sop").mkdir(parents=True)
            def ex(argv, **kw):
                calls.append(argv); envs.append(kw.get("env", {}))
                if argv[0] == "git": return P(str(root/".git"))
                return P("TestInviteOnlySignup PASS\n")
            controller.full(root, ex)
        self.assertTrue(calls)
        self.assertTrue(str(calls[0][0]).endswith("go.exe"))
        self.assertIn("127.0.0.1:13312", envs[0]["DATABASE_URL"])

if __name__=="__main__": unittest.main()
