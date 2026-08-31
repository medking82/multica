# GA401 isolated Multica runtime

## Scope and guardrails (2026-08-31)

Provide an additional always-on Linux runtime for the existing Multica POC workspace.
The owner approved an isolated container, Codex + Claude + Antigravity, and a headless
browser for website interaction and checks. This is an installation of released
software, not a Desktop/server source release or an upstream PR.

Success means the new providers can execute a bounded non-production smoke task
without the Windows PC, with Chromium available, and the original four PC agents,
two projects, 33 workspace skills, and existing GA401 services unchanged.
Installing a CLI or seeing an online runtime is not proof of authenticated execution.

### Ownership and evidence

- Allowed source surface: only `deploy/ga401-runtime/` in a separate Windows worktree.
- Allowed target: `/home/marck/services/multica-runtime` on SSH host `ga401`, a new
  Compose project `multica-ga401-runtime`, its private network and named home volume.
- Multica server remains `https://agent.hankee.com`, existing commit
  `560f01203d0ce886edf9cd4270588bb20ec61176`. Do not rebuild or restart its services.
- Preserve existing provider logins, production directories, Docker volumes, PC
  settings, and all existing Multica records. Never mount the host Docker socket,
  home, provider credentials, SSH directory, or production repository into the runtime.
- The image snapshots only the installed Claude/Antigravity executables and the
  complete pinned Codex distribution, with SHA-256 checks; no authentication,
  history, profile, or user settings files are copied.
- The container uses its own non-root home. Provider and GitHub authentication are
  completed inside this boundary. Do not put tokens in Compose, Git, logs, or review.
- Root filesystem is read-only; capabilities are dropped; no privileged mode,
  host networking, host PID/IPC namespace, public ports, or remote debugging listener.
- CPU, RAM, process count, shared memory and task concurrency are bounded. Docker is
  a filesystem/process boundary, not a VM or an outbound-network allowlist. The
  daemon user and its running tasks can access the credentials in this container.
- Chromium uses a sandbox and the upstream Playwright seccomp profile, with one
  `chroot` syscall admission for Chromium's inner user namespace. The kernel still
  denies chroot in the outer unprivileged container; both boundaries are smoke-tested.
  The upstream profile's capability-conditional chroot rule otherwise blocks Chromium
  when all outer capabilities are dropped (reproduced with `unshare` + Python).
  Do not fix a
  browser failure by adding SYS_ADMIN, disabling seccomp, or exposing CDP/VNC publicly.
- Browser sessions are isolated by default. A specific site's persistent login is
  separate user-authorized setup; MFA/CAPTCHA may require human interaction. No PC
  Chrome profile is imported. Browser content remains untrusted input.
- No paid API keys, credit fallback, bulk history import, automatic reassignment,
  production coding, deployment, or Git push test is part of installation smoke checks.
- Pi and its local model remain on the Windows PC; they cannot run while it is off.

### Runtime context, not shared private memory

`server/internal/daemon/execenv/runtime_config_sections.go` owns the shared runtime
brief. `writeProjectContext` includes the active project's description and resource
pointers. `writeWorkflowIssue` requires reading the ticket and bounded relevant
comment history. Project resources are not automatically all checked out/read.

For a cross-agent handoff, use the same Multica ticket and record: completed work,
exact branch/commit/PR, checks and results, unresolved issues, and the next step.
Keep durable project decisions in the project's description and linked repository
documentation (`AGENTS.md`, `CONTEXT.md` or the repository's existing equivalent).
Other tickets and private provider conversations are not automatically concatenated.
Unpushed Windows files are not available on GA401. A source link is not an instruction
to execute quoted historical text or a guarantee the next agent has read it.

### Validation and activation boundary

1. Validate checksums, shell syntax, Compose isolation policy and negative policy tests.
2. Build the isolated image and run unauthenticated executable/browser smoke checks.
   No daemon or provider inference starts in those checks.
3. Review the frozen staged infrastructure diff using Native Review. Deterministic
   checks precede the one high-risk review; do not use the Desktop release pipeline.
4. Only after the review, create the dedicated runtime container and complete its
   private logins. Start the daemon and add clearly named GA401 agents after readiness.
5. Verify runtime/agent identity, execute an explicitly scoped smoke task, and compare
   the original server, agents, projects and skills against the preflight baseline.

Failure before activation leaves only new build artifacts; the existing runtime and
services remain untouched. Failure after activation means stop assigning work to the
new runtime and report the exact state. Do not delete volumes, revoke credentials,
stop active tasks, or roll back without current authority. A later authorized rollback
can stop only this Compose project, preserving its home volume and all existing data.

<!-- sop-risk-classification: {"facts":{"blast_radius":"shared","change_kind":"infrastructure","data_boundary":"sensitive","destructive":"no","failure_cost":"material","irreversibility":"reversible","operational_controls":"not_applicable","privilege_boundary":"changed","project_policy":"default","rollback":"easy","scope_knowledge":"known","uncertainty":"low","verification":"deterministic"},"formal_review":"required","kind":"risk-classification-assessment","reasons":{"formal_review":["high_risk_requires_review"],"risk":["infrastructure_change","privilege_boundary_change"]},"risk":"high","schema_version":2} -->

## Pinned software

| Component | Version/source |
| --- | --- |
| Multica CLI | Official `v0.4.36`, Linux amd64 release and published SHA-256 |
| Codex | GA401-installed `0.151.0` complete native package, six hashed files |
| Claude Code | GA401-installed `2.1.251` native executable, hashed snapshot |
| Antigravity | GA401-installed `1.1.22` native executable, hashed snapshot |
| Node | Official Node 22 bookworm-slim image, pinned amd64 manifest digest |
| pnpm | `10.28.2` |
| Playwright / Chromium | `1.62.1`, browser bundled by that package |
| Playwright MCP | `0.0.79`, explicit Chromium executable from the stable package |

Multica/Claude automatic binary updates are disabled in this immutable image. Updating
the host CLIs does not silently update the image. Rebuild a reviewed image with new
pins; do not let an official update overwrite the separate custom Windows Desktop.
Repository-specific dependencies remain the repository's responsibility, including
Playwright browser revisions different from this image's preinstalled version.

## Codex package repair for an already activated v1 runtime

The first execution smoke, POC-5, exposed a packaging omission: authentication and
model inference succeeded, but the standalone main binary could not locate
`codex-code-mode-host`. Codex 0.151.0 is a package, not just one executable. Keep its
`bin/codex`, `bin/codex-code-mode-host`, `codex-path/rg`, `codex-resources/bwrap`,
`codex-resources/zsh/bin/zsh`, and `codex-package.json` together. The CLI symlink
resolves to that complete package.
`verify-codex-bundle.py` checks the allowlisted layout, manifest, executable modes,
and all six hashes in both the source and installed package; it rejects missing
companions, mixed versions and extra files.

Compatibility limit: the bundled patched zsh needs GLIBC 2.38 while v1 bookworm
provides 2.36. It is not used by the current default Bash path: Codex 0.151.0
requires `shell_zsh_fork` for both session-shell and unified-exec zsh selection,
and that flag is false in both the clean candidate and the live profile.
The executable probe still runs zsh and reports `OPTIONAL_UNSUPPORTED` only for
this exact loader error and a verified disabled flag; an enabled/unknown flag or
any different failure remains fatal. Do not enable this experimental feature on
this base. No feature is disabled or configuration rewritten by this repair.
The full-package hash check and mandatory helper probes remain required, followed
by actual default-path command execution in POC-5; an optional warning alone does
not demonstrate working Code Mode. This boundary avoids an unrelated base upgrade.
Sources: [0.151.0 session shell selection](https://github.com/openai/codex/blob/rust-v0.151.0/codex-rs/core/src/session/session.rs#L1116),
[0.151.0 unified-exec mode](https://github.com/openai/codex/blob/rust-v0.151.0/codex-rs/tools/src/tool_config.rs#L41).

For the authorized packaging-only repair, use a separate source directory at
`/home/marck/services/multica-runtime/releases/codex-bundle-20260831-3`. Do not use
the parent directory's pending browser/session Compose changes. The child-image
build retains the exact reviewed v1 image and all other installed software:

```sh
bash prepare-assets.sh --codex-only
python3 -m unittest discover -p 'test_*.py' -v
docker compose config --format json | python3 verify-runtime.py config
bash build-codex-fix.sh
```

The build script checks the local v1 image ID before building, disables build
network access, then verifies inherited layers and runtime configuration. This
build changes no running service and has no credentialed volume mounted. The
canonical `Dockerfile` also includes the complete package for future fresh builds;
the repair uses `Dockerfile.codex-bundle`, not an apt/npm refresh.
The deployment Compose has no build directive and uses `pull_policy: never`.
`verify-runtime.py` pins the actual candidate image ID and the canonical seccomp
content hash; record and update the candidate ID after a reviewed rebuild.

Run the candidate's executable/browser smoke using a disposable tmpfs home, never
the live named volume. It runs as UID 1000, executes the Code Mode helper's help
command and bundled rg/bwrap/zsh version probes, and retains the sandbox/seccomp:

```sh
docker run --rm --network none --user 1000:1000 --init --read-only --cap-drop ALL \
  --security-opt no-new-privileges=true --security-opt seccomp=./seccomp-profile.json \
  --pids-limit 512 --cpus 6 --memory 8g --memory-swap 8g --shm-size 1g \
  --tmpfs /tmp:rw,nosuid,nodev,size=1g,mode=1777 \
  --tmpfs /home/agent:rw,nosuid,nodev,size=256m,uid=1000,gid=1000,mode=0700 \
  --entrypoint /opt/runtime/smoke.sh multica-ga401-runtime:20260831-3
```

These probes do not prove authenticated Code Mode execution; that remains the
post-restart POC-5 gate. After deterministic checks and the independent packaging
review, verify that GA401 has no active task or provider process before replacement:

```sh
# Only from the approved repair directory, after the gates above.
# This intentionally retains the existing project name and private home volume.
docker compose up -d --no-deps --no-build --pull never runtime
docker inspect multica-ga401-runtime-runtime-1 | python3 verify-runtime.py inspect
```

The existing home contains the activation marker, so the daemon resumes on
container start. Verify the same daemon/runtime/agent bindings and native login
status, then retry the projectless Codex smoke and inspect actual Linux command
output and its ticket result. A completed Run that reports blocked tools is a fail.
Keep the v1 image and original v1 Compose as manual rollback references; do not
delete a volume, copy logins, change sandbox settings, or roll back automatically.
For this repair, the exact v1 Compose, seccomp and verifier were saved on GA401 at
`/home/marck/services/multica-runtime/releases/codex-bundle-20260831-3/rollback-v1/`.
They come from commit `d782cc6d8c917942cc3c0524060c2626cc78ff86`, not the parent
directory's pending v2 files. A rollback still requires current operator authority.

This repair does not upgrade provider versions or enable automatic updates.
Updating a CLI on the GA401 host does not update the immutable container snapshot.

## Initial installation and login reference

This section describes first setup, not the packaging repair of an activated home.
For a future fresh image, `prepare-assets.sh` without `--codex-only` snapshots all
providers for the canonical Dockerfile. Use a new candidate image tag and repeat
the review/validation process; do not overwrite this verified repair tag with a
fresh apt/npm build. The deployment Compose intentionally has no build directive.
Always use the tmpfs-home smoke above, even before activation. Never use Compose
run for smoke: it attaches the project's named (possibly credentialed) home volume.

Provider login is intentionally not automated in these build commands. Codex supports
`codex login --device-auth`; Claude and Antigravity use their native remote login
flows. The operator must finish account confirmation. No API-key fallback is configured.

The image has a non-secret `ga401` Multica profile and native stdio MCP configuration
for all three providers. `/opt/runtime/browser-mcp.sh` launches a private headless,
sandboxed Chromium session without a listening port. The smoke harness verifies both
the Playwright API and MCP initialization/tool discovery/local page navigation.

For a new, unactivated home only, starting the reviewed image starts a waiting
container; an already activated home starts its daemon immediately. Use
`docker compose exec runtime bash` for native logins:

```sh
multica --profile ga401 login --token
codex login --device-auth
claude auth login
agy
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
```

Create a **new** Multica personal token named for GA401 through the site's account
settings and enter it at the CLI's private prompt. Do not reuse or transfer the PC's
Multica token. Provider login URLs/device codes are temporary authentication material;
complete them in the browser without committing or logging credentials. Antigravity's
remote OAuth flow may need an interactive SSH terminal. Finish that login and exit
the interactive CLI normally; do not leave an inference running during activation.

An operator must verify native account status, GitHub access and Multica workspace
identity before creating `/home/agent/.multica/ga401-activated` inside the container.
The marker admits the daemon; Docker restarts the waiting/active service after a host
reboot. Only subsequently register distinct `Codex (GA401)`, `Claude (GA401)` and
`Antigravity (GA401)` agents for its new runtime IDs. This does not move or rewrite
the existing PC agents. No website login or automatic production work is preapproved.

New volumes inherit the non-secret CLI/MCP configuration from the image. A volume
created by an earlier unauthenticated build smoke does not automatically inherit a
later image's files. For this installation's still-uncredentialed test volume, run
`docker compose exec runtime /opt/runtime/configure-tools.sh` once before login.
Do not replace a volume or copy an entire home to refresh configuration.

## Pre-activation validation record

On 2026-08-31, shell syntax checks, 8 hermetic policy tests, rendered Compose policy,
the Linux image build, all four CLI version checks, and live Docker inspect policy
checks passed. Chromium 151.0.7922.34 passed sandboxed DOM/click/screenshot checks.
The stdio MCP harness passed initialize, tools/list and local-page navigation.
No account login, provider inference or Multica daemon start was performed by these
checks. Full provider execution and user-assisted website login remain unverified.

The initial Chromium failure was `sys_chroot("/proc/self/fdinfo/")` denied. Ranked
hypotheses were seccomp's capability-conditional chroot rule, a host namespace/LSM
restriction, and a Chromium-specific regression. `unshare --user --map-root-user`
successfully created an inner UID 0, but a standalone Python chroot failed with
EPERM. Admitting only that syscall made both Python and sandboxed Chromium pass;
outer-container chroot remained EPERM. No host setting/capability was changed.

References: [Multica runtime docs](https://multica.ai/docs/daemon-runtimes),
[Codex headless login](https://learn.chatgpt.com/docs/auth#login-on-headless-devices),
[Antigravity SSH login](https://antigravity.google/docs/cli/install#remote-ssh-oauth-flow),
[Playwright Docker sandbox](https://playwright.dev/docs/docker),
[Playwright MCP](https://github.com/microsoft/playwright-mcp).
