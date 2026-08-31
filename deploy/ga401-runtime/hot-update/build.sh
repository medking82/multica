#!/bin/sh
set -eu
cd "$(dirname "$0")"
test "$(docker image inspect -f '{{.Id}}' multica-ga401-runtime:20260831-3)" = \
  sha256:fdb46cfe7d838a3c7c246106ca2fa330fcb9578b915b58adb264f2d222562f94
test -z "$(docker ps -aq --filter ancestor=multica-ga401-runtime:20260831-hot2)"
docker build --network=none --pull=false -t multica-ga401-runtime:20260831-hot2 .
docker image inspect multica-ga401-runtime:20260831-3 multica-ga401-runtime:20260831-hot2 \
  | python3 verify_policy.py inheritance
