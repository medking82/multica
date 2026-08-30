# Windows custom Desktop channel

This fork's `codex/desktop-custom` branch combines Workspace Skill `/` picking
with native Codex dictation. It does not enable the transcription API draft,
copy ChatGPT credentials, or alter server accounts, projects, agents, or data.

## Feature contract

- Chat, issue comments, replies, manual ticket creation, and agent-assisted
  ticket creation use the same Workspace Skill picker and native mic adapter.
- Skills come from the workspace Skills library, not an automatic scan of
  arbitrary PC skill directories. Existing `/note` and editor commands remain.
- Mic is Windows Desktop-only. The user must configure Codex global dictation
  as `Ctrl+Alt+Shift+D`. Multica focuses the current editable composer and sends
  that fixed chord through its bundled, foreground/identity-checked helper.
- Codex owns audio capture and transcription. Multica neither sends audio to
  GA401 nor calls an OpenAI-compatible paid transcription endpoint. Availability
  and any subscription limits still depend on the user's Codex account.
- The helper's success means the shortcut was dispatched, not that audio was
  recognized or text was inserted. A real audio smoke test is user-operated.

## Update flow

`official stable release -> isolated merge candidate -> tests -> x64 installer
-> final ASAR/CLI/composer checks -> complete draft -> fork latest feed`

`Custom Desktop Release` runs manually or every six hours (GitHub schedules can
be delayed). It reads the latest **stable release**, not moving upstream `main`.
Fork `main` remains the upstream-sync branch; the custom branch owns patches.
The three controller files on `main` enable scheduled runs:

- `.github/workflows/custom-desktop.yml`
- `scripts/custom-desktop/release.mjs`
- `scripts/custom-desktop/release-policy.mjs`

The Windows build job has only read access and no persisted checkout credential.
The separate publication job runs the controller at the workflow's immutable SHA,
without installing candidate dependencies or executing candidate payload code.
Bootstrap reads the controller blobs from the exact SOP release commit, never
from local working files. Publication fetches both canonical parent commits and
recomputes the merge tree before a fast-forward push to `codex/desktop-custom`;
matching commit parents alone is not accepted.
Only a successful build can publish. It rechecks branch/upstream identity, exact
assets and hashes, verifies GitHub upload digests, then publishes the draft and
moves the `latest` feed. Merge/test/upload conflicts fail closed; there is no
automatic conflict resolution, retry, or rollback. An unfinished draft requires
operator recovery. Changes to existing controller files require a separate,
explicit controller update; bootstrap will not overwrite them.

Versions are `<upstream version>-custom.<workflow run number>`, tagged
`desktop-v<version>`. Published custom releases are not GitHub prereleases.
The build passes that same immutable version as `MULTICA_CLI_VERSION` to the Go
bundler, independently of reachable Git tags. This explicit input accepts stable
or custom Desktop semantic versions; malformed values or missing Go fail closed.
Normal builds without the variable keep their existing git-derived CLI version.
Before publication, the final packaged CLI's actual `--version` must match the
plan exactly, and the manifest records this checked `inspection.cliVersion`.
The client is pinned to `medking82/multica`, `latest`, with prereleases and
downgrades disabled. Official updater settings must not replace this feed.

With automatic updates enabled, Desktop checks at startup and hourly and
downloads in the background. On Windows, installation is deferred if a bundled
CLI process is present or its status cannot be checked. The update check never
stops an agent. Wait for runs to finish and stop the Desktop runtime, then quit
or choose **Restart now**. Closing a busy Desktop can leave the runtime running
under its existing preference; that intentionally also leaves the update pending.

## Validation and limitations

`node scripts/custom-desktop/check.mjs full` runs the targeted shared/desktop
regressions, typechecks, control-policy tests, and isolated helper/CLI tests.
Go CLI tests receive dedicated `HOME` **and** `USERPROFILE` plus existing build
caches. The build guard examines the actual `app.asar`, all five composed editor
bindings, native-only adapter, bundled x64 CLI, feed configuration, and hashes.
The metadata conversion is checked with electron-updater's actual YAML parser;
an offline GitHubProvider test covers custom tag and asset URL resolution. The
Node release tests can also run directly with `node --test
scripts/custom-desktop/*.test.mjs` after installing dependencies and Go 1.26 on
any platform. The tagless-checkout regression compiles only a tiny local Go
fixture with network and toolchain downloads disabled, not a real agent.
Bundled guards intentionally require unminified output, explicitly pinned in
`electron.vite.config.ts`. A future compiler/name change can stop publication and
requires an inspected guard update; it must not weaken negative regression tests.
Existing Windows worktrees must keep `apps/desktop/scripts/package.mjs`'s hashbang LF-terminated
(`.gitattributes` pins fresh checkouts); fix that file's line endings if its guard
fails, without discarding local edits or resetting unrelated source.
No default test invokes a real agent, microphone, or account. Source greps alone
are not packaged UI acceptance. After installation, verify `/` menus and mic
visibility; a user must confirm real dictation and a later-version upgrade.

These builds are **unsigned**. SHA-256/SHA-512 and HTTPS protect transport and
artifact consistency; they do not provide Authenticode publisher identity.
The GitHub fork/workflow is therefore a trusted software-update authority.
Do not disable Windows security prompts or configure a fake certificate.

Before manual installation, verify the published manifest and installer digest,
make a private backup of the exact current app/userData/CLI profile, confirm the
runtime is idle, and stop only that Desktop-owned runtime. Preserve account
profiles, Codex/Claude/Antigravity registrations, and projects. Do not upload
backups or tokens to GitHub. Rollback requires a human-selected exact backup.

For release commands and first-time bootstrap, see [SOP](../.sop/README.md).
