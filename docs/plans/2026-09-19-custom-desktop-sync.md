# Custom Desktop stable upstream sync repair

## Request and evidence

Repair the repeated GitHub failures identified in the user's Hotmail notifications.
Custom Desktop Release run 35421910302 fails during candidate preparation: the
custom branch cannot automatically merge the latest stable upstream release.
The selected stable tag is `v0.5.0`, annotated tag object
`7973633c5c91eaa404fda4e3f37f9a8997771ea2`, which the official GitHub tag API
resolves to commit `2df765a3c8f39789c9fb76316378bcffc20d22d9`. The user accepted
this exact tagged baseline and all-custom-differences review on 2026-09-19.
The custom parent is
`7823c7a9b2c6907298d3392a059315e8ee0a2163`.

## SYNC-1: reconcile the stable release with the fork's custom features

- Resolve the real merge without weakening automatic conflict detection.
- Retain all five Workspace Skill picker and native dictation consumers.
- Keep selected Skill grants server-owned and transactional with token issuance,
  delivered-comment receipts and upstream issue snapshots. Preserve the new
  locked-runtime delivery authorization and the named/derived run-origin gate.
- Keep upstream session renewal and the fork's injected dictation provider.
- Regenerate sqlc from the resolved queries; verify the affected claim, prompt,
  service and UI behavior plus the canonical custom Desktop checks.
- Publish only the fork's custom Windows Desktop channel through `.sop/sop.py`.
  No server deployment, account changes or upstream publication is in scope.
- Success means the scheduled/manual Custom Desktop Release can prepare, validate,
  package and publish the stable release with both custom features intact.

<!-- sop-risk-classification: {"facts":{"blast_radius":"shared","change_kind":"schema_or_data_migration","data_boundary":"sensitive","destructive":"no","failure_cost":"material","irreversibility":"reversible","operational_controls":"not_applicable","privilege_boundary":"changed","project_policy":"default","rollback":"easy","scope_knowledge":"known","uncertainty":"material","verification":"deterministic"},"formal_review":"required","kind":"risk-classification-assessment","reasons":{"formal_review":["high_risk_requires_review"],"risk":["schema_or_data_migration","privilege_boundary_change"]},"risk":"high","schema_version":2} -->

## Workspace ownership

Owner repository: `C:\github\tools\multica`. Isolated checkout:
`C:\github\worktrees\multica\sync-v050-20260919`, branch
`fix/custom-desktop-sync-v050`. The primary checkout's unrelated voice-input
changes remain untouched. Retire this checkout only after the custom branch and
published release contain its work, the exact result is verified, and its local
test environment is no longer needed. Preserve failed-run and review evidence.
