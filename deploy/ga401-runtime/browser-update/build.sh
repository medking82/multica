#!/bin/sh
set -eu
cd "$(dirname "$0")"
test "$(docker image inspect -f '{{.Id}}' multica-ga401-runtime:20260831-hot2)" = \
  sha256:d69f3f5f17ae3c19f731e6a8dd0c915497c93629920b32716f9a8cb15b2e3082
test "$(docker image inspect -f '{{.Id}}' multica-ga401-browser:20260831-1)" = \
  sha256:cebf16fb1624877aa17d5a3232a475fadb01c60c20f6937b680426c789cd9edc
test -z "$(docker ps -aq --filter ancestor=multica-ga401-runtime:20260831-browser1)"
test -z "$(docker ps -aq --filter ancestor=multica-ga401-browser:20260831-browser1)"
docker build --network=none --pull=false --target runtime -t multica-ga401-runtime:20260831-browser1 .
docker build --network=none --pull=false --target login -t multica-ga401-browser:20260831-browser1 .
docker image inspect multica-ga401-runtime:20260831-hot2 multica-ga401-runtime:20260831-browser1 \
  multica-ga401-browser:20260831-1 multica-ga401-browser:20260831-browser1 \
  | python3 verify_browser_policy.py inheritance
