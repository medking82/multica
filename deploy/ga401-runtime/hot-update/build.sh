#!/bin/sh
set -eu
cd "$(dirname "$0")"
test "$(docker image inspect -f '{{.Id}}' multica-ga401-runtime:20260923-2)" = \
  sha256:01699a2210055a13f623e7c38ff80aafce5972e0911edf336293aa653147055e
test -z "$(docker ps -aq --filter ancestor=multica-ga401-runtime:20260923-hot1)"
docker build --network=none --pull=false -t multica-ga401-runtime:20260923-hot1 .
docker image inspect multica-ga401-runtime:20260923-2 multica-ga401-runtime:20260923-hot1 \
  | python3 verify_policy.py inheritance
