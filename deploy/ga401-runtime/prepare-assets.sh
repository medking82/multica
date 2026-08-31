#!/bin/bash
set -euo pipefail
umask 077
target=/home/marck/services/multica-runtime
case "$(pwd -P)" in
  "$target"|"$target/releases/codex-bundle-20260831-3") ;;
  *) echo "Run only inside the approved runtime or Codex fix directory." >&2; exit 1 ;;
esac
[[ "$#" == 0 || ( "$#" == 1 && "${1:-}" == --codex-only ) ]] || exit 1
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

# Snapshot the complete native distribution. The main binary locates its Code Mode
# helper/resources relative to the package, not through the system PATH.
bundle_source=/home/marck/.local/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl
# Reconcile the allowlist with the source too: do not silently omit a new companion.
python3 verify-codex-bundle.py "$bundle_source"
for directory in .assets/codex-bundle .assets/codex-bundle/bin \
                 .assets/codex-bundle/codex-path .assets/codex-bundle/codex-resources \
                 .assets/codex-bundle/codex-resources/zsh .assets/codex-bundle/codex-resources/zsh/bin; do
  [[ ! -L "$directory" ]] || { echo 'Refusing symlinked package directory.' >&2; exit 1; }
  install -d -m 0755 "$directory"
done
# Git archives produced on Windows may give the checksum list CRLF endings.
while IFS=$' \t\r' read -r expected relative; do
  case "$relative" in
    bin/codex|bin/codex-code-mode-host|codex-path/rg|codex-resources/bwrap|codex-resources/zsh/bin/zsh|codex-package.json) ;;
    *) echo 'Unexpected package member.' >&2; exit 1 ;;
  esac
  snapshot "$bundle_source/$relative" "codex-bundle/$relative" "$expected"
done < codex-bundle.sha256
chmod 0644 .assets/codex-bundle/codex-package.json
python3 verify-codex-bundle.py .assets/codex-bundle
[[ "${1:-}" != --codex-only ]] || exit 0

snapshot /home/marck/.local/bin/agy agy 2822292f90deea4556938a8728fe4ed02a1d66d1525cf75fa07a171e36a38c25
snapshot /home/marck/.local/share/claude/versions/2.1.251 claude fd5f10ff0eb58daec04900466b143ea98aab50abf208a422bc008eaec13f61f7

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
