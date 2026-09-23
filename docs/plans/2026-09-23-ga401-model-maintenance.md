# GA401 Multica model maintenance repair

## Outcome and boundaries

The user asks to resolve stale GPT-6 Astra / Opus 5.5 models in GA401-Isolated
and the failed automatic update path. Desktop discovery already returns both
models. Preserve existing agents, accounts, runtime HOME, browser integration,
custom slash/native voice features, task data and ongoing runs.

The GA401 host update is separately complete at ga401-v29. This repair concerns
the isolated Multica runtime and its existing update owners. No host CLI change,
model inference, credentials export, new login, automatic rollback, volume
deletion or wholesale Multica upstream merge belongs to this bounded repair.

## Evidence and owners

- Existing app updater: `deploy/ga401-upgrade/cycle.py`, immutable installer,
  Windows task `Multica-GA401-UpstreamUpdate`, branch `codex/ga401-upgrade-0439`.
- Git owner: `C:\github\tools\multica`; retained automation checkout:
  `C:\github\tools\upstream\multica-ga401`. Retirement condition: scheduler
  unenrolled or moved through a verified ownership migration. This is not a
  disposable release checkout.
- Installed source is pinned to 84af632561b1cb39d1742ae6b2cb2728793dc12e.
  Its wrapper names a retired checkout. Historical cycle state additionally
  records `needs_attention` at merge, because upstream changed protected owners.
  Preserve that evidence; repairing the path cannot clear the hold.
- Runtime CLI owner: existing isolated provider updater / offline validator.
  Codex 0.151.0 remains active after the 0.155.1 bundled zsh ABI check failed.
  The container uses Debian 12; the validator requires a base-image repair.
  Claude follows the publisher's `stable` channel, currently behind `latest`.

## Planned verification

1. Repair the exact checkout binding and let the installer preserve an explicit
   existing physical installation root when run under Codex MSIX.
2. Preserve branch, remote, immutable source and failed-cycle guards. Reuse
   HardwarePulse Claude metadata and native Gemini metadata for admitted review.
3. Repair runtime compatibility and Claude channel through its existing source,
   offline validation and activation owners. Never skip the failing ABI check.
4. Run affected deterministic tests, then one Native Review of the frozen
   infrastructure diff. Source publication, activation and any old failed-cycle
   continuation are separately recorded and must use the established owners.
5. Acceptance requires actual GA401-Isolated API discovery returning
   `gpt-6-astra` and `claude-opus-5-5`, preserved runtime/agent identity, and
   truthful scheduler state. A repaired path with an unresolved historical hold
   is not a healthy automatic app updater.

Classification: deterministic risk-classification.py returned `high`,
`formal_review=required`, reason `infrastructure_change`; shared infrastructure,
sensitive existing state, unchanged privilege boundary, reversible activation,
easy image rollback, material failure cost, known scope and low uncertainty.

## Windows scheduler guardrails (implementation admitted 2026-09-23)

The Windows repair is independently reviewable from the container image and PR
#7730 candidates. Its allowed surface is `deploy/ga401-upgrade`: checkout binding,
source installer, task visibility/status, persistent build-tool paths, and their
tests/docs. Provider image changes have a separate runtime-owner worktree.
Keep committed-source extraction, current-user limited identity, six-hour cadence,
manifest verification, exclusive cycle lock, terminal failed-cycle hold, and all
review/release gates. Do not restart the old cycle or alter its recorded state.

The Temp Go and pnpm shim dependencies are gone. Use the checksum-verified official
Go 1.26.8 portable toolchain at `C:\github\tools\upstream\toolchains\go1.26.8`
and a repository-owned shim into the existing pnpm runtime, preserving package.json's
10.28.2 selection (the global fallback shim forces its bundled 11.x instead).
Unit tests use fake tool files so their
success does not depend on an installed workstation toolchain. Real tool version
and hidden-launch checks remain separate acceptance evidence.

Use a windowless script host for the scheduled entry point, retaining the canonical
hidden Python runner. Status resolves the registered wrapper's physical root,
including the existing MSIX installation; it never assumes process LOCALAPPDATA.
Validation: updater unit tests, actual hidden-host success/failure probes,
PowerShell parser checks, physical-root status fixtures, and Git whitespace checks.
Activation requires exact reviewed committed source; preserve the previous wrapper,
task definition and state before replacing their binding. Rollback is not automatic.

<!-- sop-risk-classification: {"facts":{"blast_radius":"shared","change_kind":"infrastructure","data_boundary":"sensitive","destructive":"no","failure_cost":"material","irreversibility":"reversible","operational_controls":"not_applicable","privilege_boundary":"unchanged","project_policy":"default","rollback":"easy","scope_knowledge":"known","uncertainty":"low","verification":"deterministic"},"formal_review":"required","kind":"risk-classification-assessment","reasons":{"formal_review":["high_risk_requires_review"],"risk":["infrastructure_change"]},"risk":"high","schema_version":2} -->
