"""Windows owner for the bounded GA401 upgrade phases."""
from __future__ import annotations
import argparse, hashlib, json, os, re, subprocess, shlex, sys
from pathlib import Path

HEX40 = __import__('re').compile(r"^[0-9a-f]{40}$")
REMOTE = "/home/marck/services/multica/upgrades"
SCRIPT = "deploy/ga401-upgrade/upgrade.py"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

class Failure(RuntimeError): pass

def run(argv, cwd=None, env=None, executor=subprocess.run):
    p = executor(argv, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
                 stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
    if p.returncode:
        raise Failure(f"command failed ({p.returncode}): {argv[0]}")
    return p.stdout or ""

def git(repo: Path, *args, executor=subprocess.run):
    return run(["git", *args], cwd=repo, executor=executor).strip()

def ssh(host: str, command: str, executor=subprocess.run):
    return run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host, command], executor=executor)

def scp(local: Path, host: str, remote: str, executor=subprocess.run):
    return run(["scp", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", str(local), f"{host}:{remote}"], executor=executor)

def digest(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1<<20), b""): h.update(b)
    return h.hexdigest()

def readiness(repo: Path, executor=subprocess.run) -> None:
    run(["git", "diff", "--check"], cwd=repo, executor=executor)
    run(["git", "diff", "--cached", "--check"], cwd=repo, executor=executor)
    ssh("ga401", "true", executor)

def common_dir(repo, executor=subprocess.run):
    path = Path(git(repo, "rev-parse", "--git-common-dir", executor=executor))
    return (repo / path).resolve()

def quick(repo: Path, executor=subprocess.run) -> None:
    for test in ("test_upgrade.py", "test_controller.py"):
        print(run([sys.executable, "-B", "-m", "unittest", "-v", test], cwd=repo / "deploy/ga401-upgrade", executor=executor), flush=True)
    preserved = git(repo, 'diff', '--name-only', 'f42a0a4678f3aa8ecba981f542d3ef3b66996249', '--',
                    'packages/', 'apps/', 'server/cmd/multica/', executor=executor)
    if preserved:
        raise Failure('shared /, voice, or runtime baseline code changed')

def full(repo: Path, executor=subprocess.run) -> None:
    go = Path(r"C:\Users\Marck\AppData\Local\Temp\multica-go-1.26.6\go\bin\go.exe")
    if not go.is_file(): raise Failure("portable Go toolchain is missing")
    env={**os.environ, "DATABASE_URL":"postgres://multica_fixture:multica_fixture_local@127.0.0.1:13312/multica_repair?sslmode=disable"}
    output = run([str(go), "test", "./internal/handler", "-run", r"^Test(InviteOnlySignup.*|CheckSignupAllowed.*)$", "-count=1", "-v", "-timeout=5m"], cwd=repo / "server", env=env, executor=executor)
    if "TestInviteOnlySignup" not in output or "PASS" not in output or re.search(r"(?i)skip|skipping", output):
        raise Failure("full gate did not prove invite-only tests passed without skips")
    common = common_dir(repo, executor)
    (common / "sop").mkdir(parents=True, exist_ok=True)
    (common / "sop" / "ga401-upgrade-full.log").write_text(output, encoding="utf-8")

def _commit(repo: Path, env=None, executor=subprocess.run) -> str:
    value=(env or os.environ).get("SOP_RELEASE_COMMIT")
    if not HEX40.fullmatch(value or ""): raise Failure("SOP_RELEASE_COMMIT must be an exact 40-hex SHA")
    head=git(repo, "rev-parse", "HEAD", executor=executor)
    if head != value: raise Failure("SOP_RELEASE_COMMIT does not match HEAD")
    return value

def deploy(repo: Path, state_path: Path, env=None, executor=subprocess.run) -> dict:
    commit=_commit(repo, env, executor)
    common=common_dir(repo, executor)
    archive=common / "sop" / "ga401-upgrade" / f"{commit}.tar"
    archive.parent.mkdir(parents=True, exist_ok=True)
    with archive.open("xb") as out:
        p=subprocess.Popen(["git", "archive", "--format=tar", "--prefix=source/", commit], cwd=repo, stdout=out, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        _, err=p.communicate()
        if p.returncode: raise Failure("git archive failed")
    archive_sha=digest(archive); root=f"{REMOTE}/{commit}"
    ssh("ga401", f"umask 077; mkdir -p {REMOTE} && mkdir {root}", executor)
    scp(archive, "ga401", f"{root}/source.tar", executor)
    remote_sha=ssh("ga401", f"sha256sum {root}/source.tar", executor).split()[0]
    if remote_sha != archive_sha: raise Failure("remote source archive hash mismatch")
    extract = "import tarfile,os; os.umask(0o077); t=tarfile.open(%s); names=t.getnames(); assert all((n == 'source' or n.startswith('source/')) and '..' not in n.split('/') for n in names); assert all(not (i.issym() or i.islnk()) for i in t.getmembers()); t.extractall(%s, filter='data')" % (repr(f"{root}/source.tar"), repr(root))
    ssh("ga401", f"python3 -c {shlex.quote(extract)}", executor)
    commands=[("preflight", f"python3 {root}/source/{SCRIPT} preflight --release-root {root} --source-commit {commit}"),
              ("build", f"python3 {root}/source/{SCRIPT} build --release-root {root} --source-commit {commit}"),
              ("rehearse", f"python3 {root}/source/{SCRIPT} rehearse --release-root {root} --source-commit {commit}"),
              ("activate", f"python3 {root}/source/{SCRIPT} activate --release-root {root} --source-commit {commit}")]
    receipts={}
    for phase, command in commands:
        print('GA401 phase: ' + phase, flush=True)
        receipts[phase]=ssh("ga401", command, executor)
        print(receipts[phase], flush=True)
    result={"commit":commit,"archive":str(archive),"archive_sha256":archive_sha,"remote_root":root,"receipts":{k:digest_text(v) for k,v in receipts.items()}}
    common_state = common / "sop" / "ga401-upgrade-state.json"
    common_state.parent.mkdir(parents=True, exist_ok=True); common_state.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    return result

def digest_text(value: str) -> str: return hashlib.sha256(value.encode()).hexdigest()

def verify(repo: Path, executor=subprocess.run) -> str:
    commit = _commit(repo, executor=executor)
    state_path = common_dir(repo, executor) / 'sop' / 'ga401-upgrade-state.json'
    state=json.loads(state_path.read_text(encoding="utf-8"))
    root=state.get("remote_root")
    if state.get('commit') != commit or root != f"{REMOTE}/{commit}": raise Failure("deploy state has invalid source binding")
    return ssh("ga401", f"python3 {root}/source/{SCRIPT} verify --release-root {root} --source-commit {commit}", executor)

def main(argv=None) -> int:
    p=argparse.ArgumentParser(); p.add_argument("mode", choices=["readiness","quick","full","deploy","verify"]); p.add_argument("--repo", default="."); p.add_argument("--state", default="upgrade-controller.json"); a=p.parse_args(argv)
    repo=Path(a.repo).resolve(); state=Path(a.state)
    try:
        if a.mode=="readiness": readiness(repo)
        elif a.mode=="quick": quick(repo)
        elif a.mode=="full": full(repo)
        elif a.mode=="deploy": deploy(repo,state)
        else: print(verify(repo), flush=True)
        return 0
    except Exception as exc: print(f"FAILED: {exc}", file=__import__('sys').stderr); return 1
if __name__=="__main__": raise SystemExit(main())
