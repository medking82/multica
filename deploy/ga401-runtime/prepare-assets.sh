#!/bin/bash
set -euo pipefail
umask 077
target=/home/marck/services/multica-runtime
[[ "$(pwd -P)" == "$target" ]] || { echo "Run only inside $target" >&2; exit 1; }
[[ "$(id -u)" == 1000 ]] || { echo 'Run as marck, not root.' >&2; exit 1; }
[[ "$(uname -m)" == x86_64 ]] || exit 1
[[ ! -L .assets ]] || { echo 'Refusing symlinked asset directory.' >&2; exit 1; }
mkdir -p .assets

snapshot() {
  local source=$1 name=$2 expected=$3
  [[ -f "$source" ]] || { echo "Missing executable: $source" >&2; exit 1; }
  printf '%s  %s\n' "$expected" "$source" | sha256sum --check --status
  if [[ -e ".assets/$name" || -L ".assets/$name" ]]; then
    [[ -f ".assets/$name" && ! -L ".assets/$name" ]] || exit 1
    printf '%s  %s\n' "$expected" ".assets/$name" | sha256sum --check --status
  else
    install -m 0755 "$source" ".assets/$name"
    printf '%s  %s\n' "$expected" ".assets/$name" | sha256sum --check --status
  fi
  printf '%s\n' "$name: pinned executable snapshot verified"
}

snapshot /home/marck/.local/bin/agy agy 2822292f90deea4556938a8728fe4ed02a1d66d1525cf75fa07a171e36a38c25
snapshot /home/marck/.local/share/claude/versions/2.1.251 claude fd5f10ff0eb58daec04900466b143ea98aab50abf208a422bc008eaec13f61f7
snapshot /home/marck/.local/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex codex 9739cbc928b9c573be83256acd46668f5dd4f119d2d09e05246895ca2aaf0c9a

archive=.assets/multica-cli-0.4.36-linux-amd64.tar.gz
if [[ ! -e "$archive" ]]; then
  curl --fail --location --proto '=https' --tlsv1.2 --max-time 120 \
    https://github.com/multica-ai/multica/releases/download/v0.4.36/multica-cli-0.4.36-linux-amd64.tar.gz \
    --output "$archive"
fi
[[ -f "$archive" && ! -L "$archive" ]] || exit 1
printf '%s  %s\n' bdee5c7f574202e43d9cafe23914a384ad4e86098b98f59432faed6fdc92bfa2 "$archive" | sha256sum --check --status
for member in multica LICENSE NOTICE; do
  [[ ! -L ".assets/$member" ]] || exit 1
done
tar --extract --gzip --file "$archive" --directory .assets --no-same-owner --no-same-permissions multica LICENSE NOTICE
chmod 0755 .assets/multica
printf '%s\n' 'Multica v0.4.36 release checksum verified; no credentials were copied.'
