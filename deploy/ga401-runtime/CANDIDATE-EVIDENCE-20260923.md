# GA401 browser/runtime candidate evidence — 2026-09-23

This is a review and promotion plan for disposable candidate `runtime-candidate-20260923-2`.
Native Review completed on frozen tree `3edb89a87dd2c484a17e6ced8cfc76f6ee558ff0`
(packet `902ed287a95ee468a950a866af544fe7e1f2fa38e5a3c9c1a726da0afa88f81a`).
Its P2 staging-permission and P3 recovery-documentation findings were accepted and
fixed with deterministic checks. There was no second model round and no production
activation, publication or rollback. The image IDs below are the rebuilt final
artifacts after those fixes, not the original review's artifacts.

## Provenance and source boundary

- Canonical owner: branch `codex/ga401-browser-update`, HEAD `b7b9495fbb7f7ef62bb5a316206a3218ccf16d41`.
- Deployed browser baseline: commit `0f6b0545a3e5329de189b4eecfd94ac001820fcb`
  (parent `66e76b620`); follow-up PID-headroom fix: `b7b9495fbb7f7ef62bb5a316206a3218ccf16d41`.
- Retained source: `/home/marck/services/multica-runtime/releases/browser-update-20260831-1/browser-update`.
  The committed baseline contains 20 files; the 18 operational source files and their
  source SHA-256 values are preserved below. The two test files are retained by the
  baseline commit and are covered by the Linux test run.

The retained license inputs required by the fresh-build guard are
`.assets/LICENSE` SHA-256 `0e42d37bb02dc61f270c5a0528d489da76e5a578b209856f2e95ee4d60aacdbe`
and `.assets/NOTICE` SHA-256 `763619b43ae4f18c43bef5284c04a5739f84cd9f935c0123cf67834541ec3d9a`.

| file | source SHA-256 |
|---|---|
| `.dockerignore` | `7f236f615f18d92c2c1336041e494d9954c941061a3792fad98b00f560142a79` |
| `Dockerfile` | `3066e6ab3f74d0edfaa4ec43977c480e4debb7ccb6d912b6411385d8ebd5bc7c` |
| `README.md` | `bbe064c3b9e5678f8b40b1744ce7e3f52fdd8fc60502ddef01cfa8f351f53b20` |
| `browser_common.py` | `bf574e861976a02733db40b44909ef607daf432a8895379649dad47e901e36b6` |
| `browser_launcher.py` | `7b344d6d8ce711df8cb5153527ae9094053f4dc73551678d0bdab033048e139b` |
| `browser_probe.cjs` | `92ff7f2d8e518ef571ff40a87e148846c97b0ee95088c7c112282eaa9f10ca89` |
| `browser_updater.py` | `a79a6324e1a57d80c980ca53afaa8ad3233dfca3f343ba75a754d03d81ad6227` |
| `browser_validator.py` | `ed01073b281ef3b3236cae01fdc5d84d7f108e003b7cd1517e2343e43281879c` |
| `browser_watch.py` | `35ea17c078ff14c7d1f116e40b4e323e7a0862c6e30e8ce5c158c86e1deea6e9` |
| `build.sh` | `1968ffaab31460e85cf5c51692e7c1bc29b8bfc3ef250dda702bf7fea986fc2` |
| `compose.login.yaml` | `43df335d46971b7c5a03c66cd5a0f3800f985b06b080594f71014b2dc3e650ee` |
| `compose.yaml` | `4054bc8a40148b47aa0d460a835c291d7cfee91f8f75f8ffe4550edea2ca13db` |
| `launch-mcp.sh` | `f7926c977079dff0a6a70c60b391ab98d459d9282f68d71e061cc1e839fdbf09` |
| `launch-playwright.sh` | `4d4eae86fa3103776734ebb99a820fd498c4e7d2238f35e07d3f33d5c85c400a` |
| `profile_guard.py` | `544ec0098aeb278fb61810db328771d43cbc630a728f322da4d9179c26caee93` |
| `seed_browser.py` | `1ead95eaccedd483fcf5ffe3171b1cda7014aad1d7f6f1009701a8a5032728fb` |
| `verified-images.json` | `41538d6de297ac151bfbbf790b4ceba714bd3eed51c42e73a9635b4bbd4e79f2` |
| `verify_browser_policy.py` | `38244ec481225e239b7bc2e76b342986eefe56d7564999f484dc476c8e697822` |

## Candidate images

| layer | immutable image ID |
|---|---|
| runtime base (`20260923-2`) | `sha256:01699a2210055a13f623e7c38ff80aafce5972e0911edf336293aa653147055e` |
| signed CLI hot layer (`20260923-hot1`) | `sha256:1397ca113bbeb891ef92e30f1f07c338ced175aaa7f98ef9e1ceee20e17751a5` |
| browser runtime (`20260923-browser1`) | `sha256:1a3b42bbfa63702d21717c23e522eb64c973fc35238a2447ec13295fa1129738` |
| browser login (`20260923-browser1`) | `sha256:d98078c71f2aac8811365109b61d7d71554ec3128613daa7fef264873d6091c5` |

## Checks completed

- The parent rebuilt from genuinely empty `.assets`, ran the complete current
  `prepare-assets.sh`, checked every Codex package directory is 0755, and rebuilt
  all three layers. The final base passed non-root full CLI/Chromium/MCP smoke.
  GA401 uses uutils coreutils 0.8.0: its intermediate-directory modes under umask
  077 differ from the earlier unverified GNU-install assumption. Every ancestor
  is now explicitly prepared after the complete symlink check.
- Windows archive creation must use `git -c core.autocrlf=false archive` for the
  runtime subtree. Otherwise root `.gitattributes` can be lost when archiving a
  subtree and automatic CRLF conversion can break executable shell shebangs.
  The final reconstruction and builds used LF source without changing host Git
  settings or product shell scripts.
- Final post-review repetition copied production public tools read-only into
  labelled `ga401-parent-final-tools-20260923`, used separate validator results,
  and completed real download/validate/promote plus wrapper execution on the final
  image. Before/after were `0.151.0/2.1.267/1.2.8` and
  `0.156.1/2.1.280/1.2.9`. Final browser images passed both consumer probes against
  the read-only production public browser generation with synthetic tmpfs profiles.
  Logs: `parent-post-review-cli-*.log`, `parent-post-review-browser-*.log`,
  `parent-base-smoke-after-review.log`, `parent-fresh-directory-modes.log` and
  `parent-final-*-suite.log`.
- Final base/package policy suite: 31 tests; CLI hot-update suite: 27 tests;
  browser suite: 38 tests. Obsolete optional-zsh exception tests were replaced by
  strict ABI-failure checks and the full voice-companion inventory regression.
- Final runtime wrappers using the promoted disposable old volume read-only,
  network none and tmpfs HOME returned Codex `0.156.1`, Claude `2.1.280`, and
  Antigravity `1.2.9`. Codex warned that temp-HOME aliases could not be created;
  the native version command exited successfully. No account state was mounted.
- Ancestor symlink staging refusal passed and the external target remained
  untouched. Both exact candidate Compose policies passed without any override.
- Browser policy validation passed for runtime and login Compose files.
- Full browser overlay suite ran on GA401 (`posix`, uid 1000): 38 tests passed, 0 skipped;
  the Linux generation/profile subset was 15 tests passed, 0 skipped. This includes old-generation retention, both-validator admission,
  changed-binary refusal, failed-download hold, and profile isolation checks.
- Candidate restricted smoke passed with `--network none`, read-only root, uid 1000,
  dropped capabilities, no-new-privileges and the candidate seccomp profile: Chromium
  sandbox and stdio Playwright MCP initialization/tool discovery/local navigation.
- The parent reran both final image consumer probes as offline uid 1002 with
  dropped capabilities, read-only root, pinned seccomp and tmpfs HOME. The existing
  public browser volume was mounted read-only; generation `pw-1.63.0-mcp-0.0.82`
  passed runtime headless/full-profile and login headed/profile-restart checks,
  plus MCP initialize/tools/navigation/close for both consumers. Raw logs:
  `parent-final-browser-runtime.log` and `parent-final-browser-login.log`.
- Candidate base/hot CLI probes passed for Codex `0.156.1`, Claude `2.1.280`,
  Antigravity `1.2.8`, and Multica `0.4.39-ga401.84af6325`.
- The 15-test old-generation promotion cases are synthetic unit fixtures, not a live
  updater rehearsal; they did not mount `runtime-home`, account data, or production
  `cli-tools` volumes.
- The final real upgrade rehearsal copied only the production public `cli-tools`
  volume through a read-only mount into `ga401-live-old-tools-20260923`. No account
  home was mounted. Before: Codex `0.151.0`, Claude `2.1.267`, Antigravity `1.2.8`.
  Actual publisher discovery/download, network-none validation and a second updater
  pass promoted all three to `0.156.1`, `2.1.280`, `1.2.9`; every provider reported
  `status=updated`, and final pointers matched. Old and new native version reports
  all passed. Antigravity `1.2.9` appeared during this task; the image's retained
  `1.2.8` seed does not prevent the independently signed/checksummed latest update.
- Earlier synthetic old-version metadata around new binaries produced native
  version mismatches and missing-lock promotion failures. Those `old-flow-*` logs
  are retained as failed checks, not real old-generation evidence. An earlier
  real-download attempt exposed missing `gpgv`; the base explicitly installs it
  and the final signed Claude download succeeded without bypassing verification.
- Raw successful logs are under the remote candidate's `evidence-logs/`:
  `clean-prepare.log`, `real-old-flow-updater-fetch.log`,
  `real-old-flow-validator.log`, `real-old-flow-updater-promote.log`,
  `real-old-flow-after-pointers.log`. Build logs and failed attempts are retained.
- Fresh `prepare-assets.sh` staging was intentionally fail-closed: the host Codex
  source contained unallowlisted `codex-resources/voice/*`; the official
  `@openai/codex@0.156.1-linux-x64` artifact was then verified from npm
  (`sha512-2ePo0wgOcnONKsuzp8vBjOmNY+IdsKaouaDDIdiKq9HOWNuV/GI22OeXft2A/1GaoL71ictO0/pLAsCEnQ6wew==`,
  SHA-512 hex `d9e3e8d3080e72738d2acbb3a7cbc18ce98d63e21db0a6a8b9a0c321d88aabd1ce58db95fc6236d8e7977edd80ff519aa0bef589cb4ed3fa4b02c0849d0eb07b`),
  and its complete 44-file inventory, including voice companions, was pinned.
- Activation uses the candidate's two Compose files directly. The earlier
  experimental `runtime-image-candidate.json` contains an obsolete image ID and
  is not part of the promotion input. The production app's old image override
  must not be combined with the new runtime Compose: it would select the old ABI.
  Image tags remain accepted by the existing policy only with the exact local
  image IDs recorded in `verified-images.json`; no image-policy relaxation is used.
- Local `git diff --check` passed before staging.

## Risk and promotion gate

<!-- sop-risk-classification: {"facts":{"blast_radius":"shared","change_kind":"infrastructure","data_boundary":"sensitive","destructive":"no","failure_cost":"material","irreversibility":"reversible","operational_controls":"not_applicable","privilege_boundary":"unchanged","project_policy":"default","rollback":"costly","scope_knowledge":"known","uncertainty":"low","verification":"deterministic"},"formal_review":"required","kind":"risk-classification-assessment","reasons":{"formal_review":["high_risk_requires_review"],"risk":["infrastructure_change","material_failure_with_costly_rollback"]},"risk":"high","schema_version":2} -->

This is high-risk infrastructure work: it changes a runtime base ABI, signed CLI
update channel and browser image while preserving the existing volume and identity
boundaries. Native Review and explicit activation authority remain required.

The app upgrade owner currently binds the historical runtime Compose directory.
Its September 4 merge hold remains in place. A future app-upgrade resume must
rebind that owner to the new runtime deployment and revalidate its exact base;
this candidate does not authorize or silently repair/resume an app cutover.

Validate the candidate without any app image override:

```sh
cd /home/marck/services/multica-runtime/releases/runtime-candidate-20260923-2
docker compose -f browser-update/compose.yaml config --format json |
  python3 browser-update/verify_browser_policy.py config
docker compose -f browser-update/compose.login.yaml config --format json |
  python3 browser-update/verify_browser_policy.py config
```

After separate activation authority, verify both image tags against the exact
`verified-images.json` IDs before any container recreation. Capture the current
container IDs, Compose labels, image IDs, native versions, runtime identities and
public generation pointers. Require an idle Multica Run snapshot, then stop only
the runtime container before changing public generations. This bounded daemon
pause prevents the old ABI from launching a newly promoted Codex while validators
and image recreation are in progress. If a Run is active, defer the cutover.

Recreate only the two runtime maintenance services, the browser validator, and
the runtime using these candidate files (no build, pull, app override or login):

```sh
docker stop --time 120 multica-ga401-runtime-runtime-1
docker compose -f browser-update/compose.yaml up -d --no-deps --no-build --pull never updater validator
docker compose -f browser-update/compose.login.yaml up -d --no-deps --no-build --pull never validator
# Wait for all three CLI reports to pass under the new validator identity,
# promotion to complete, and both browser validator reports to pass.
# Confirm runtime remains stopped and no incomplete assigned Run appeared.
docker compose -f browser-update/compose.yaml up -d --no-deps --no-build --pull never runtime
docker inspect multica-ga401-runtime-runtime-1 multica-ga401-runtime-updater-1 \
  multica-ga401-runtime-validator-1 multica-ga401-browser-validator-1 |
  python3 browser-update/verify_browser_policy.py inspect-runtime
```

Use only the existing updater/validator owners for report generation and promotion;
never edit an artifact pointer by hand. Check successful current CLI reports, exact
native wrapper versions and Multica model discovery after recreation.
The `runtime-home`, `cli-tools`, `cli-validation`, `browser-tools` volumes and the
release pointer remain unchanged; container IDs may change on recreation and are
health-check outputs, not identity. With the login browser held unchanged, verify
the exact four active service contracts (runtime, updater, runtime validator, and
browser validator) with `inspect-runtime`, then run updater status, native model
discovery and browser/MCP health checks. The login browser remains outside this
activation unless separately authorized and confirmed idle; its later switch requires
the full five-service `inspect` check.

Rollback to the old ABI is unsupported after CLI promotion by this maintenance
owner. Retaining old release directories and image IDs does not restore the old
current pointers, and the updater intentionally cannot downgrade. If activation
fails after promotion, keep the runtime stopped and preserve all evidence until
an explicitly authorized correction on the new ABI passes validation. Do not
restart the old image against the newly promoted CLI volume, change pointers by
hand, or imply that a generation/profile backup was created by this task. A future
old-base recovery needs a separately designed, authorized and validated restoration
of mutually compatible artifact generations. This is a material recovery limit,
so rollback is classified as costly rather than easy.

No production activation, pointer mutation, release publication, or rollback was
performed by this candidate task.
