# GA401 Chromium auto-update

Extends the deployed provider CLI updater without altering its three update
policies. This directory owns browser maintenance only. It does not publish a
Multica release, update Windows Desktop, or deploy the pending session exporter.

## What updates

Every 24 hours the GA401 updater checks official stable `playwright` and
`@playwright/mcp` packages. It installs both with their exact declared dependencies
and the matching Chromium/full headless-shell/FFmpeg artifacts into a new browser
generation. MCP's own publisher-pinned prerelease Playwright dependency is kept;
the browser executable is explicitly selected from stable Playwright.

This follows the latest **Playwright stable browser bundle**, not Chrome Canary or
every independent Chrome patch. Repository-local Playwright installations still
own their own dependencies/browsers. OS packages, Node and Multica are unchanged;
a new browser requiring incompatible system libraries holds with a visible error.

The downloader validates npm SHA512 SRI, restricts HTTPS/redirect destinations,
refuses ranged/new dependencies and archive links/path escapes, and never executes
npm lifecycle scripts, `apt`, `curl | sh` or the browser installer. The only native
package discovery command is the public Playwright `install --dry-run chromium`;
its three exact destinations are checked before downloading/extracting archives.
Browser archives are trusted via publisher HTTPS, then locally SHA256-inventoried.
Those local hashes are **not** an independently published signature.

## Validation and next-session switch

- A runtime-base validator and a login-browser-base validator must both pass full
  inventory checks, sandboxed Chromium DOM/click/screenshot, synthetic profile
  restart/persistence, and stdio MCP local navigation. Login validation also runs
  Chromium headed under a temporary Xvfb. Neither sees an account/profile volume
  or has an external network. Reports bind the exact generation, checker and base;
  they expire after 24 hours and refresh every 12 hours.
- Promotion atomically replaces only the `current` symlink. Each wrapper resolves
  it once to a complete immutable generation; Node also resolves its loaded module
  realpath. Its private `.local-browsers` stays alongside that module. Old files
  are not modified/deleted and existing browser/MCP sessions are never killed.
- A new MCP process/global Playwright invocation uses the new generation. A
  long-lived MCP process stays on its loaded generation until that process closes.
- The human noVNC browser changes only at its **next container start**. An open
  browser is not forcibly restarted, so it can show the previous version until
  the user finishes and restarts it. Its original `restart: no` policy remains.

## Account and process boundaries

The additional `multica-ga401-runtime_browser-tools` volume contains public code,
browser assets, hashes and update status only. Updater UID 1001 has RW access;
runtime, human browser and validators have RO access. The two validators use UID
1002 and the existing validation-results volume, without account mounts/network.

`browser_watch.py` calls the existing provider updater/validator functions unchanged,
then the browser functions. A browser error does not skip a provider cycle; a
provider error does not skip a browser cycle. Neither maintenance process receives
Docker authority, account environment, Chrome Sync state, browser cookies or tokens.

The human browser keeps its private profile and state volumes, original entrypoint
and private bridge. Before a new Chromium first opens its existing profile,
`profile_guard.py` takes a private backup under
`/home/browser/browser-update-backups/`. It excludes only transient singleton links,
never exports logins, and refuses linked/special profile files, downgrade, concurrent
startup and insufficient disk. An interrupted backup is retained as `.incomplete-*`;
Chromium is not started in that case. Backups require operator-managed retention.
`browser-update-attempt.json` records an attempted launch, not proof of account login.

The existing v1 session exporter is **not approved for real-session export** by this
change. Do not use it to share Google/GitHub state, attach the profile/state volume
to agents, alter browser sandbox flags, or expose CDP/VNC publicly.

## Checks and first deployment

Source is an independent Windows worktree. Stage only this directory. Export the
`deploy/ga401-runtime` subtree with `git -c core.autocrlf=false archive`, to preserve
LF without changing Windows Git settings. Use a new GA401 release directory, never
the parent service directory's pending session-export Compose files.

```text
python -m unittest discover -s deploy/ga401-runtime/browser-update -p test_*.py -v
python -m unittest discover -s deploy/ga401-runtime/hot-update -p test_*.py -v
python -m unittest discover -s deploy/ga401-runtime -p test_*.py -v
node --check deploy/ga401-runtime/browser-update/browser_probe.cjs
```

Repeat the Linux-only mode/lease/profile tests in a disposable Linux container.
Linux lifecycle fixtures in the previous provider suite need test-only executable
tmpfs; production `/tmp` remains noexec. Then, on GA401 in this directory:

```sh
docker compose config --format json | python3 verify_browser_policy.py config
docker compose -f compose.login.yaml config --format json | python3 verify_browser_policy.py config
sh build.sh
```

The child builds are network-free and retain the exact verified parent layers.
Record `build.sh`'s image IDs/config in `verified-images.json`. Use disposable
tools/results/home mounts for acquisition and both native smokes before one frozen
Native Review. Never smoke-test against the real profile. The repository SOP
release router is Desktop-only; do not run it to publish/restart these services.

After deterministic checks and Native Review, an authorized deployment can create
the shared public browser volume and start the maintenance services, require both
seed validation reports, then replace only the idle runtime. Its original home,
CLI volume, daemon identity and all agents/projects/Skills are retained.

The first human-browser replacement additionally requires confirmation that the
user has finished unsaved page work. Gracefully stop the old browser; verify its
process exited, preserve both exact volumes, then use this child login image. No
forced active browser/task stop is permitted. Resolve its actual private IP again
if a PC SSH tunnel needs reconnecting. Do not print the VNC access code or cookies.

Before that switch, inspect only owner/mode metadata of `/home/browser` and
`/home/browser/chromium-profile` in the existing container: both must be UID 1000
and mode 0700. This was verified on 2026-08-31; do not assume a recreated volume has
the same permissions. A mismatch blocks activation for an explicit operator repair;
do not relax the guard or automatically chmod an unknown account volume.

After the authorized replacements:

```sh
docker inspect multica-ga401-runtime-runtime-1 multica-ga401-runtime-updater-1 \
  multica-ga401-runtime-validator-1 multica-ga401-browser-login-1 \
  multica-ga401-browser-validator-1 | python3 verify_browser_policy.py inspect
docker compose exec -T updater python3 /opt/runtime/browser-update/browser_watch.py updater status
```

Compare redacted pre/post agent bindings, project/Skill identities and every other
container. Native browser tests are synthetic, not proof that Google/GitHub still
accept a particular saved session. The human may need to reauthenticate if a site
invalidates it; never work around MFA or login challenges.

If human-browser restart confirmation is pending, deploy only runtime, updater and
both validators. Inspect those exact four containers with `inspect-runtime` instead
of `inspect`; it requires all four under the same image/mount/security checks. Leave
the live `login` container completely unchanged and report human-browser activation
as pending, not completed. Full `inspect` remains mandatory after its later switch.

## Failure and recovery

All update errors retain the current generation. Failures do not auto-downgrade,
delete old releases/backups, rebuild a base image or consume model/API quota. A
normal SIGTERM unwinds only the update's newly created staging directory; power
loss/SIGKILL can leave `.download-*` directories for exact-path operator cleanup.
At least 6 GiB free is required before download; private backup additionally needs
the measured profile size plus 2 GiB. Status is public JSON and bounded logs.

Deployment failure stops the rollout; do not silently retry or roll back. Keep the
previous runtime Compose at `releases/cli-hot-update-20260831-2/hot-update`, its
`20260831-hot2` image, and the previous human-browser image `20260831-1`. Manual
rollback requires current operator authority and retention of every original
volume. A migrated browser profile must be restored from its matching protected
backup before a downgrade; selecting an old image alone is not a safe rollback.

References: [Playwright browser/version management](https://playwright.dev/docs/browsers),
[Playwright MCP](https://github.com/microsoft/playwright-mcp),
[Chrome for Testing](https://developer.chrome.com/docs/automation-and-testing/chrome-for-testing).
