# Repository-local SOP launcher

`.sop/sop.py` is the official `agent-sop-kit` project launcher. It delegates to
the installed shared runtime (`../agent-sop-kit`, or `AGENT_SOP_KIT_ROOT`).
This fork has an existing upstream `AGENTS.md`, so it deliberately does **not**
claim a managed initializer installation, overwrite instructions, or install
global tools/hooks. `workflow.json` belongs to this repository.

Run natively on Windows with Python 3, Node, Git, GitHub CLI, the repository's
pnpm 10.28.2, Go 1.26.x, and installed pnpm dependencies. Use the Windows hidden
runner for background commands. Do not run tests against real CLI profiles.

```text
python .sop/sop.py --repo . doctor
python .sop/sop.py --repo . check --quick
python .sop/sop.py --repo . check --full
python .sop/sop.py --repo . autotrigger --inspect
```

The workflow is specifically for the authorized `medking82/multica` Windows
custom Desktop branch, not a server deployment. Before the first release,
the clean custom branch's base must already exist on the fork remote. Freeze
the intended staged diff, complete deterministic checks and the required
high-risk independent review, then use `release-runner --launch`.

SOP owns preflight, quick/full, commit, push, CI, verify, and notify. Its CI
stage bootstraps only the three declared controller paths onto fork `main`
(missing files only; different existing content stops), dispatches the custom
workflow, and waits using `actions-watch` bound to the release commit. A
failure is persisted and requires explicit remediation/resume authority.
There is no automatic rollback, force-push, or production mutation.

See [custom Desktop operations](../docs/custom-desktop.md).
