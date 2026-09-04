# GA401 Multica automatic updates

The user authorized scheduled upstream updates retaining slash skill selection,
native voice, invite-only signup, accounts, provider configuration, browser
registration and durable data. This permits one normal release per eligible
stable upstream version, never failure resume, rollback, review bypass or credits.

## Schedule and ownership

`Multica-GA401-UpstreamUpdate` runs every six hours in Windows Task Scheduler,
under the current signed-in user with limited privileges. Windows, SSH to
`ga401`, Docker Desktop and declared build tools must be available. Missed runs
start when Windows is available again. This is not a GA401 systemd timer and
never wakes a Codex conversation.

`install-scheduler.ps1` extracts exact committed files into a versioned directory
under `%LOCALAPPDATA%\MulticaAutoUpdate`. Every invocation verifies their SHA-256
hashes. The reserved checkout is `C:\github\multica-ga401-upgrade-0439`, branch
`codex/ga401-upgrade-0439`. User edits, remote drift, an incomplete SOP run or a
previous failed/interrupted cycle stop before further changes. Inspect task
status and next run with `scheduler-status.ps1`; receipts and command logs live
in the installation root's `state` directory. No email is sent by this kit.

## Update cycle

`discover.py` reads the latest official published stable release, resolves tags
to exact commits and detects tag movement. An unchanged release updates local
status only: no model, dependency install, remote build or production restart.

For a new release, `cycle.py` binds the completed deployed receipt and makes a
real two-parent Git merge. It never rsyncs over source. Conflicts, non-increasing
versions, divergent upstream history or changes to instruction files, `.sop`,
the deployment owner or custom feature checks require an operator.

The candidate passes the existing custom Desktop quick/full gates (slash and
voice contracts, regression tests and typechecks) and real invite-only tests
against an isolated local PostgreSQL fixture. The canonical risk classifier
admits this shared server release to one Native Review of the full staged diff.
Future upstream releases are not implicitly trusted review baselines. Native
quota selection is prepared by Native Review's metadata-only `prepare-quota.py`
immediately after checks. The full upstream-merge task admits Opus/high and native
Gemini Flash/high; Sonnet remains available for separately admitted bounded reviews,
not automatically for this full merge. Optional fresh Claude evidence lives in
`state/claude-quota-input.json` using Native Review's selection schema with Opus
only and account alias `local-main`. Missing evidence is unknown, never 100%.
The helper otherwise reads the independent native Gemini Models pool, including
five-hour and weekly windows/reset, and resolves its exact current Flash/high ID.
All windows must exceed the configured reserve. Input snapshots use unique local
filenames; the Native Review archive binds the selected route and input digest.
No eligible candidate or a metadata failure pauses before inference. A selected
review is one attempt without runtime fallback. An oversized packet, failed
review or unresolved finding pauses the cycle without retry or substitution.

The canonical SOP release runner owns commit, push, CI and verify. Review
provides evidence; the user's recurring-update instruction provides release
authority. Completion requires a successful SOP run and matching live receipt.
There is deliberately no automatic resume or state-reset command.

## Deployment and data preservation

`transition.json` binds prior source, exact image IDs and migration ledger to
the target official version/commit. `upgrade.py` requires the referenced prior
receipt to be complete. Read-only `snapshot` validates live source, images,
ledger and Compose/container contracts without modifying deployment state.

Phases are preflight, build, rehearse, activate and verify. Images use the exact
committed archive. Runtime inherits the previous filesystem/configuration and
replaces only the Multica executable. A private dump is restored and migrated
in isolated PostgreSQL 17 before cutover. Activation rechecks idle admission,
stops writers and retains a final database dump plus uploads/runtime backups.

Only backend, frontend and the named runtime are replaced through separate
image overrides. PostgreSQL, original Compose/env and existing volumes remain.
Verify checks health, readiness, public routing, runtime version, invite-only
configuration, durable row IDs and volume contracts. The health client declares
its identity instead of changing Cloudflare security policy.

No volume deletion, prune, automatic rollback, router/DNS edit or provider
updater change occurs. Slash/native voice checks preserve the current feature
boundary; they do not constitute a physical microphone test.
