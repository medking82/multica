#!/bin/bash
set -euo pipefail
target=/home/marck/services/multica-runtime/releases/codex-bundle-20260831-3
[[ "$(pwd -P)" == "$target" && "$(id -u)" == 1000 ]] || exit 1
base=multica-ga401-runtime:20260831-1
expected=sha256:b30ec9ff066de2b0188fda7c2e1258f2782b530377d07257e3410495046c7287
[[ "$(docker image inspect "$base" --format '{{.Id}}')" == "$expected" ]] || exit 1
docker compose config --format json | python3 verify-runtime.py config
python3 verify-codex-bundle.py .assets/codex-bundle
docker build --pull=false --network=none --file Dockerfile.codex-bundle \
  --tag multica-ga401-runtime:20260831-3 .
python3 - <<'PY'
import json
import subprocess

base, fixed = json.loads(subprocess.check_output([
    'docker', 'image', 'inspect',
    'multica-ga401-runtime:20260831-1', 'multica-ga401-runtime:20260831-3',
]))
assert base['Id'] == 'sha256:b30ec9ff066de2b0188fda7c2e1258f2782b530377d07257e3410495046c7287'
assert fixed['RootFS']['Layers'][:len(base['RootFS']['Layers'])] == base['RootFS']['Layers']
for key in ('User', 'Env', 'Entrypoint', 'Cmd', 'WorkingDir', 'Volumes', 'ExposedPorts'):
    assert fixed['Config'].get(key) == base['Config'].get(key), 'Changed runtime config: ' + key
print('Pinned v1 inheritance PASS; candidate image ' + fixed['Id'])
PY
