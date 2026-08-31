# GA401 provider CLI hot update

Deployment-only extension of the reviewed `20260831-3` runtime. It keeps the
Multica daemon, base OS/Node/Chromium, provider accounts and private runtime home.
The custom Windows Desktop and the separate signed-in browser are not involved.

## Update contract

- Once per 24 hours, discover official stable Codex, Claude and Antigravity CLI
  releases. Claude's `stable` channel may lag `latest`; never downgrade an existing
  newer version. No prereleases or runtime/model/provider reassignment.
- Download to staging, check publisher integrity, unpack complete packages, then
  atomically install a *new* version directory. No `curl | sh`, in-place overwrite,
  global `npm install`, user config change or third-party inference.
- A separate offline validator checks inventory/checksums, CLI versions/flags and
  Codex's helper/App Server initialization contract. Its report is bound to the
  exact record, checker code and base image, and expires after 24 hours. These
  probes deliberately do not consume subscription quota or prove model quality.
- The updater retries a pending promotion every minute. A shared process-lifetime
  lock in each runtime launcher prevents the exclusive pointer switch while that
  provider is running. Parallel Runs share the lease. Only the next invocation
  sees the new complete version; helpers and the old binary stay together.
- Errors, missing/failed reports, full disk and ABI changes hold the old version.
  Old versions and images are retained; rollback/deletion requires an operator.

The updater runs as UID 1001 with tools RW and validation reports RO. The validator
runs as UID 1002 with tools RO and reports RW, `network_mode: none`, a clean tmpfs
HOME per check and no account volume. Runtime stays UID 1000 with tools RO and its
original home RW. All three have readonly root, dropped capabilities, the existing
seccomp policy, resource bounds, and no Docker socket/host mount/published port.
The updater's bridge is separate from the runtime bridge. No new sandbox exception.

Publisher trust: Codex uses SHA512 SRI from the official npm registry; Claude uses
SHA256 from a GPG-signed manifest with pinned fingerprint
`31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE`; Antigravity uses SHA512 from the official
installer's manifest. HTTPS redirects and artifact paths are allowlisted. These
checks trust the publishers and their distribution infrastructure; they cannot
prove that a publisher release is bug-free or that an account will still log in.

Codex 0.151.0 retains the earlier documented optional zsh GLIBC_2.38 exception only
with a verified disabled `shell_zsh_fork`. Later versions must pass the probe or
wait for a base-image update; no feature setting is silently disabled. Basic
Bash/Code Mode execution was independently verified by the preceding POC-5 repair.

Multica resolves symlinks at startup, so its three CLI entrypoints are fixed regular
wrapper files, not a `current` symlink. Multica's version metadata refresh is about
10 minutes; displayed version/model metadata can lag the effective next-launch
binary. Changes requiring a new Multica backend/protocol need separate maintenance.

## Build and validation (not deployment)

Run the repository checks on Windows first; Linux-only lease/process tests are
explicitly skipped there and must pass in a disposable Linux container:

```text
python -m unittest discover -s deploy/ga401-runtime -p test_*.py -v
python -m unittest discover -s deploy/ga401-runtime/hot-update -p test_*.py -v
```

Linux lifecycle fixtures execute fake CLIs from a temporary directory. Give only
that disposable test container an executable tmpfs (`/tmp:rw,exec,nosuid,nodev`),
or an equivalent private fixture volume. Production `/tmp` remains `noexec`.

Stage only the bounded deployment files, export the `deploy/ga401-runtime` subtree
with per-command `git -c core.autocrlf=false archive`, and transfer it to a new
approved release directory. Never use the parent GA401 service directory's pending
browser/session Compose. In the exported `hot-update` directory:

```sh
python3 -m unittest discover -p 'test_*.py' -v
docker compose config --format json | python3 verify_policy.py config
sh build.sh
```

Build uses the exact local parent image, no build network and no auth volume.
Record the resulting image ID in `verify_policy.py` before freezing the Native
Review packet. A tag alone is not validation. Checks must include temporary
uncredentialed tools/results volumes, offline native probes, readonly-mount tests,
actual official package downloads/signature verification and the existing sandboxed
Chromium/MCP smoke. Never run build/test commands against the live account home.

## Deployment and operation

Only after checks and Native Review, create the two maintenance services, verify
seed reports, check zero active GA401 tasks/processes, then replace only `runtime`:

```sh
docker compose up -d --no-build --pull never updater validator
# Require three successful seed validation reports and a current idle snapshot.
docker compose up -d --no-deps --no-build --pull never runtime
docker inspect multica-ga401-runtime-runtime-1 multica-ga401-runtime-updater-1 \
  multica-ga401-runtime-validator-1 | python3 verify_policy.py inspect
docker compose exec -T updater python3 /opt/runtime/hot-update/updater.py status
```

Compare redacted agent/runtime bindings, projects/Skills and other containers with
the pre-deploy baseline. Check native launches and accounts without exposing tokens.
An optional manual discovery cycle is `docker compose exec -T updater python3
/opt/runtime/hot-update/updater.py once`; it still obeys every validation/idle gate.
Routine status is in the updater log and `/opt/agent-tools/status.json`; validation
failures include a bounded error, never an auth token or an inference transcript.
Fresh validator reports avoid full package scans on each minute tick; due checks
and every actual promotion still verify all checksums. Normal SIGTERM/SIGINT
shutdown unwinds the active download directory. SIGKILL/power loss cannot run that
cleanup: stop the updater, inspect the private tools volume's top-level
`.download-*` directories, and obtain operator approval for each exact stale path
before manual removal. Never remove `releases`, lock files or a current download.

For manual rollback, first obtain operator authority, stop the two maintenance
services, drain active Runs, and use the retained previous exact image/Compose at
`/home/marck/services/multica-runtime/releases/codex-bundle-20260831-3`. Keep the same
home volume. Do not delete any volume or silently downgrade provider data.

## Base image maintenance

This service cannot update libc/Node/Chromium or replace Docker containers. Those
need a fresh reviewed image, smoke checks and an idle replacement. The proposed
weekly check + operator-confirmed base switch is a separate, pending scheduling
decision; it is **not** enabled by this CLI service. No unattended Docker authority
is granted to the updater. A blocked ABI update remains visible until that work is
approved/completed.

Sources: [Codex CLI](https://developers.openai.com/codex/cli),
[Claude install and signing](https://code.claude.com/docs/en/setup),
[Antigravity install](https://antigravity.google/docs/cli/install).
