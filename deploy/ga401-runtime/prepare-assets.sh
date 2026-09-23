#!/bin/bash
set -euo pipefail
umask 077
target=/home/marck/services/multica-runtime
case "$(pwd -P)" in
  "$target"|"$target/releases/codex-bundle-20260831-3"|"$target/releases/runtime-candidate-20260923-2") ;;
  *) echo "Run only inside the approved runtime or Codex fix directory." >&2; exit 1 ;;
esac
[[ "$#" == 0 || ( "$#" == 1 && "${1:-}" == --codex-only ) ]] || exit 1
[[ "$(id -u)" == 1000 ]] || { echo 'Run as marck, not root.' >&2; exit 1; }
[[ "$(uname -m)" == x86_64 ]] || exit 1
[[ ! -L .assets ]] || { echo 'Refusing symlinked asset directory.' >&2; exit 1; }
mkdir -p .assets

snapshot() {
  local source=$1 name=$2 expected=$3
  local mode=0755
  case "$name" in *.json|*.md|*.txt) mode=0644 ;; esac
  [[ -f "$source" ]] || { echo "Missing executable: $source" >&2; exit 1; }
  printf '%s  %s\n' "$expected" "$source" | sha256sum --check --status
  if [[ -e ".assets/$name" || -L ".assets/$name" ]]; then
    [[ -f ".assets/$name" && ! -L ".assets/$name" ]] || exit 1
    printf '%s  %s\n' "$expected" ".assets/$name" | sha256sum --check --status
  else
    install -m "$mode" "$source" ".assets/$name"
    printf '%s  %s\n' "$expected" ".assets/$name" | sha256sum --check --status
  fi
  printf '%s\n' "$name: pinned executable snapshot verified"
}

prepare_package_directories() {
  local path=$1
  while [[ "$path" == .assets* ]]; do
    [[ ! -L "$path" ]] || { echo "Refusing symlinked package directory: $path" >&2; exit 1; }
    [[ "$path" == .assets ]] && break
    path=$(dirname "$path")
  done
  # uutils install honours the private umask for intermediate directories.
  # Check the whole chain before any writes, then set every package ancestor.
  path=$1
  while [[ "$path" != .assets ]]; do
    install -d -m 0755 "$path"
    path=$(dirname "$path")
  done
}

# Snapshot the complete native distribution. The main binary locates its Code Mode
# helper/resources relative to the package, not through the system PATH.
bundle_source=/home/marck/.local/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl
# Reconcile the allowlist with the source too: do not silently omit a new companion.
python3 verify-codex-bundle.py "$bundle_source"
# Git archives produced on Windows may give the checksum list CRLF endings.
while IFS=$' \t\r' read -r expected relative; do
  [[ -n "$relative" && "$relative" != /* && "$relative" != *'..'* ]] || exit 1
  directory=".assets/codex-bundle/$(dirname "$relative")"
  prepare_package_directories "$directory"
  snapshot "$bundle_source/$relative" "codex-bundle/$relative" "$expected"
done < codex-bundle.sha256
python3 verify-codex-bundle.py .assets/codex-bundle
[[ "${1:-}" != --codex-only ]] || exit 0

for notice in LICENSE NOTICE; do
  [[ -f ".assets/$notice" && ! -L ".assets/$notice" ]] || { echo "Missing retained $notice." >&2; exit 1; }
done
printf '%s  %s\n' 0e42d37bb02dc61f270c5a0528d489da76e5a578b209856f2e95ee4d60aacdbe .assets/LICENSE | sha256sum --check --status
printf '%s  %s\n' 763619b43ae4f18c43bef5284c04a5739f84cd9f935c0123cf67834541ec3d9a .assets/NOTICE | sha256sum --check --status

snapshot /home/marck/.local/bin/agy agy c20434f0b9278196498069dac5a0a2e72bc0b5f8aebdf17c5d535b5369b76f67
snapshot /home/marck/.local/share/claude/versions/2.1.280 claude 1e08503dbdf3c2cb0d706d32f3408277388d1c76ef108673e8fe42c1b322925b

if [[ -n "${MULTICA_SOURCE:-}" ]]; then
  snapshot "$MULTICA_SOURCE" multica 96d01b201afda32dd2a509518f74ecfcf4303121ee66e422e23f1387d9be89d4
else
  echo 'MULTICA_SOURCE is required; refusing obsolete public Multica fallback.' >&2
  exit 1
fi
printf '%s\n' 'Multica executable provenance verified; no credentials were copied.'
