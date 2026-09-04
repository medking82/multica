"""Windows owner for the bounded GA401 upgrade phases."""
from __future__ import annotations
import argparse, hashlib, json, os, re, subprocess, shlex, sys, time
from pathlib import Path

HEX40 = __import__('re').compile(r"^[0-9a-f]{40}$")
REMOTE = "/home/marck/services/multica/upgrades"
SCRIPT = "deploy/ga401-upgrade/upgrade.py"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
GO = Path(r'C:\Users\Marck\AppData\Local\Temp\multica-go-1.26.6\go\bin\go.exe')
NODE = Path(r'C:\Program Files\nodejs\node.exe')
PNPM = Path(r'C:\Users\Marck\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules\pnpm\bin\pnpm.mjs')
PNPM_SHIM = Path(r'C:\Users\Marck\AppData\Local\Temp\multica-pnpm10\pnpm.cmd')
FIXTURE = 'multica-ga401-upgrade-tests'
FIXTURE_URL = 'postgres://multica_fixture:multica_fixture_local@127.0.0.1:13312/multica_repair?sslmode=disable'

def test_env():
    if not all(p.is_file() for p in (GO, NODE, PNPM, PNPM_SHIM)): raise Failure('required Windows toolchain missing')
    return {**os.environ, 'PATH': str(PNPM_SHIM.parent) + os.pathsep + str(GO.parent) + os.pathsep + os.environ['PATH'], 'DATABASE_URL': FIXTURE_URL}

class Failure(RuntimeError): pass

def run(argv, cwd=None, env=None, executor=subprocess.run):
    p = executor(argv, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
                 stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
    if p.returncode:
        error = Failure(f"command failed ({p.returncode}): {argv[0]}")
        error.output = p.stdout or ''
        raise error
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
    print(run([sys.executable, '-B', '-m', 'unittest', 'discover', '-v'], cwd=repo / 'deploy/ga401-upgrade', executor=executor), flush=True)
    custom_gate(repo, 'quick', executor)

def fixture_contract(value):
    if value['Config']['Labels'].get('io.hankee.task') != 'ga401-upgrade-0439-tests': raise Failure('fixture owner changed')
    if value['Config']['Image'] != 'pgvector/pgvector:pg17': raise Failure('fixture image changed')
    if value['HostConfig']['PortBindings'] != {'5432/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '13312'}]}: raise Failure('fixture port changed')
    if any(m['Type'] != 'tmpfs' for m in value['Mounts']): raise Failure('fixture has persistent mounts')
    env = dict(x.split('=', 1) for x in value['Config']['Env'])
    if any(env.get(k) != v for k, v in {'POSTGRES_USER': 'multica_fixture', 'POSTGRES_PASSWORD': 'multica_fixture_local', 'POSTGRES_DB': 'multica_repair'}.items()): raise Failure('fixture identity changed')

def prepare(repo: Path, executor=subprocess.run):
    env = test_env()
    common = common_dir(repo, executor) / 'sop'
    expected = digest(repo / 'pnpm-lock.yaml')
    stamp = common / 'ga401-dependencies.sha256'
    if not (repo / 'node_modules').is_dir() or not stamp.exists() or stamp.read_text().strip() != expected:
        print('Installing repository-pinned dependencies', flush=True)
        print(run([str(NODE), str(PNPM), 'install', '--frozen-lockfile'], cwd=repo, env=env, executor=executor), flush=True)
        stamp.write_text(expected + '\n')
    names = run(['docker', 'ps', '-a', '--filter', 'name=^/' + FIXTURE + '$', '--format', '{{.Names}}'], executor=executor).splitlines()
    if not names:
        run(['docker', 'run', '-d', '--name', FIXTURE, '--label', 'io.hankee.task=ga401-upgrade-0439-tests',
             '-p', '127.0.0.1:13312:5432', '--tmpfs', '/var/lib/postgresql/data:rw,size=2g',
             '-e', 'POSTGRES_USER=multica_fixture', '-e', 'POSTGRES_PASSWORD=multica_fixture_local',
             '-e', 'POSTGRES_DB=multica_repair', 'pgvector/pgvector:pg17'], executor=executor)
    if names and names != [FIXTURE]: raise Failure('ambiguous fixture container')
    value = json.loads(run(['docker', 'inspect', FIXTURE], executor=executor))[0]
    fixture_contract(value)
    if not value['State']['Running']: run(['docker', 'start', FIXTURE], executor=executor)
    # Observations only; never repeat a failed migration.
    for _ in range(30):
        p = executor(['docker', 'exec', FIXTURE, 'pg_isready', '-U', 'multica_fixture', '-d', 'multica_repair'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        if p.returncode == 0: break
        time.sleep(1)
    else: raise Failure('fixture not ready')
    run([str(GO), 'run', './cmd/migrate', 'up'], cwd=repo / 'server', env=env, executor=executor)

def full(repo: Path, executor=subprocess.run) -> None:
    custom_gate(repo, "full", executor)
    output = run([str(GO), "test", "./internal/handler", "-run", r"^Test(InviteOnlySignup.*|CheckSignupAllowed.*)$", "-count=1", "-v", "-timeout=5m"], cwd=repo / "server", env=test_env(), executor=executor)
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

def custom_gate(repo: Path, mode: str, executor=subprocess.run) -> None:
    if mode not in ("quick", "full"): raise Failure("invalid custom gate")
    check = repo / "scripts" / "custom-desktop" / "check.mjs"
    if not check.is_file(): raise Failure("custom Desktop check controller is missing")
    common = common_dir(repo, executor) / 'sop'
    try:
        output = run([str(NODE), str(check), mode], cwd=repo, env=test_env(), executor=executor)
    except Failure as error:
        (common / ('ga401-custom-' + mode + '.log')).write_text(getattr(error, 'output', ''), encoding='utf-8')
        raise
    (common / ('ga401-custom-' + mode + '.log')).write_text(output, encoding='utf-8')
    print('PASS custom Desktop ' + mode, flush=True)

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
    p=argparse.ArgumentParser(); p.add_argument("mode", choices=["readiness","prepare","quick","full","deploy","verify"]); p.add_argument("--repo", default="."); p.add_argument("--state", default="upgrade-controller.json"); a=p.parse_args(argv)
    repo=Path(a.repo).resolve(); state=Path(a.state)
    try:
        if a.mode=="readiness": readiness(repo)
        elif a.mode=='prepare': prepare(repo)
        elif a.mode=="quick": quick(repo)
        elif a.mode=="full": full(repo)
        elif a.mode=="deploy": deploy(repo,state)
        else: print(verify(repo), flush=True)
        return 0
    except Exception as exc: print(f"FAILED: {exc}", file=__import__('sys').stderr); return 1
if __name__=="__main__": raise SystemExit(main())
