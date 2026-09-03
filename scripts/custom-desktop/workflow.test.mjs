import assert from "node:assert/strict";
import { test } from "node:test";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { spawnSync } from "node:child_process";
const require = createRequire(new URL("../../packages/core/package.json", import.meta.url));
const { parse } = require("yaml");

test("Windows packaging preserves dotted override arguments through PowerShell", { skip: process.platform !== "win32" }, async () => {
  const workflow = parse(await readFile(new URL("../../.github/workflows/custom-desktop.yml", import.meta.url), "utf8"));
  const step = workflow.jobs.build.steps.find((entry) => entry.name === "Package Windows x64 custom installer");
  assert.equal(step.shell ?? "pwsh", "pwsh");
  const prefix = "node apps/desktop/scripts/package.mjs ";
  assert.ok(step.run.startsWith(prefix));
  const version = "0.4.36-custom.123";
  assert.equal(step.env.MULTICA_CLI_VERSION, "${{ steps.prepare.outputs.version }}",
    "The bundled Go CLI must receive the same immutable release version as Electron");
  // Keep the workflow's real argument spelling, but replace packaging with a
  // native argv-only probe. No build, agent, account or network is invoked.
  const probe = "& $env:MULTICA_TEST_NODE -e 'console.log(JSON.stringify(process.argv.slice(1)))' -- " + step.run.slice(prefix.length);
  const result = spawnSync("pwsh", ["-NoProfile", "-NonInteractive", "-Command", probe], {
    env: { ...process.env, MULTICA_TEST_NODE: process.execPath, CUSTOM_VERSION: version },
    encoding: "utf8", windowsHide: true, timeout: 15_000,
  });
  assert.ifError(result.error);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), [
    "--win", "--x64", "--publish", "never",
    `-c.extraMetadata.version=${version}`,
    "-c.win.signAndEditExecutable=false",
    "-c.publish.channel=latest",
  ]);
});

test("build payload is initialized at runner time before any consumer", async () => {
  const workflow = parse(await readFile(new URL("../../.github/workflows/custom-desktop.yml", import.meta.url), "utf8"));
  const build = workflow.jobs.build;
  assert.equal(build.env?.PAYLOAD, undefined, "runner context is unavailable in job-level env");
  const initializeIndex = build.steps.findIndex((step) => step.id === "payload");
  assert.ok(initializeIndex >= 0, "Initialize PAYLOAD on the allocated runner");
  const initialize = build.steps[initializeIndex];
  assert.equal(initialize.shell, "pwsh");
  assert.match(initialize.run, /"PAYLOAD=\$env:RUNNER_TEMP\/custom-desktop-payload"\s*>>\s*\$env:GITHUB_ENV/);
  for (const [index, step] of build.steps.entries()) {
    if (/\$env:PAYLOAD/.test(step.run ?? "") || step.with?.path === "${{ env.PAYLOAD }}") {
      assert.ok(index > initializeIndex, `${step.name} must run after PAYLOAD initialization`);
    }
  }
});

test("release separates read-only candidate execution from reviewed privileged publication", async () => {
  const workflow = parse(await readFile(new URL("../../.github/workflows/custom-desktop.yml", import.meta.url), "utf8"));
  assert.equal(workflow.on.schedule[0].cron, "17 */6 * * *");
  assert.equal(workflow.on.pull_request, undefined);
  assert.equal(workflow.on.pull_request_target, undefined);
  assert.equal(workflow.concurrency["cancel-in-progress"], false);
  assert.deepEqual(workflow.jobs.build.permissions, { contents: "read" });
  assert.doesNotMatch(JSON.stringify({ env: workflow.env, build: workflow.jobs.build }), /secrets\./,
    "Candidate execution must never receive a repository secret");
  const build = workflow.jobs.build.steps;
  assert.match(workflow.jobs.build.if, /github.repository == 'medking82\/multica'/);
  assert.equal(build[0].with.ref, "codex/desktop-custom");
  assert.equal(build[0].with["persist-credentials"], false);
  assert.equal(build.filter((step) => step.env?.GH_TOKEN).length, 1);
  assert.match(build.find((step) => step.env?.GH_TOKEN).run, /release.mjs prepare/);
  const names = build.map((step) => step.name);
  assert.ok(names.indexOf("Validate both features and update guards") < names.indexOf("Package Windows x64 custom installer"));
  assert.ok(names.indexOf("Inspect final ASAR, five composers, CLI, update metadata and hashes") < names.indexOf("Transfer only the verified release payload"));
  const publish = workflow.jobs.publish;
  assert.equal(publish.needs, "build");
  assert.equal(publish.permissions.contents, "write");
  assert.equal(publish.steps[0].with.ref, "${{ github.workflow_sha }}");
  assert.equal(publish.steps[0].with["fetch-depth"], 0, "Canonical ancestry must not use shallow grafts");
  assert.equal(publish.steps[0].with["persist-credentials"], false);
  assert.equal(publish.env?.GH_TOKEN, undefined, "Keep the publication credential step-scoped");
  assert.equal(publish.steps.at(-1).env.GH_TOKEN, "${{ secrets.UPSTREAM_SYNC_TOKEN }}",
    "Canonical upstream merges can update workflow files and need Workflows write");
  assert.doesNotMatch(JSON.stringify(publish.steps.slice(0, -1)), /secrets\.UPSTREAM_SYNC_TOKEN/,
    "Only the reviewed publication controller may receive the scoped token");
  assert.ok(!publish.steps.some((step) => /pnpm|npm install|candidate\.bundle\//.test(step.run ?? "")));
  assert.match(publish.steps.at(-1).run, /release.mjs publish/);
});

test("publisher checks its credential before execution and preserves controller failures", async (t) => {
  const workflow = parse(await readFile(new URL("../../.github/workflows/custom-desktop.yml", import.meta.url), "utf8"));
  const step = workflow.jobs.publish.steps.at(-1);
  assert.equal(workflow.jobs.publish["runs-on"], "ubuntu-latest");
  assert.equal(step.shell ?? "bash", "bash");
  // Execute the real run block with an inert shell function. This cannot invoke
  // the controller, GitHub CLI, Git or a network request, and has no real token.
  const script = `node() { printf '%s\\n' "$@"; return "$MULTICA_TEST_PUBLISH_EXIT"; }\n${step.run}`;
  const bash = process.platform === "win32" ? "C:\\Program Files\\Git\\bin\\bash.exe" : "bash";
  for (const [name, token, controllerExit] of [
    ["missing token", "", 0],
    ["configured token", "fixture-not-a-credential", 0],
    ["controller failure", "fixture-not-a-credential", 42],
  ]) {
    await t.test(name, () => {
      const result = spawnSync(bash, ["--noprofile", "--norc", "-e", "-c", script], {
        env: {
          PATH: process.env.PATH,
          ...(process.platform === "win32" ? { SystemRoot: process.env.SystemRoot } : {}),
          GH_TOKEN: token, PAYLOAD: "/tmp/verified payload", MULTICA_TEST_PUBLISH_EXIT: String(controllerExit),
        },
        encoding: "utf8", windowsHide: true, timeout: 10_000,
      });
      assert.ifError(result.error);
      if (!token) {
        assert.equal(result.status, 1, "A missing secret must stop before the controller runs");
        assert.match(result.stdout + result.stderr, /UPSTREAM_SYNC_TOKEN.*Contents.*Workflows/);
        assert.doesNotMatch(result.stdout, /scripts\/custom-desktop\/release\.mjs/);
      } else {
        assert.equal(result.status, controllerExit, result.stderr);
        assert.deepEqual(result.stdout.trim().split(/\r?\n/), [
          "scripts/custom-desktop/release.mjs", "publish", "/tmp/verified payload",
        ]);
        assert.doesNotMatch(result.stdout + result.stderr, /fixture-not-a-credential/);
      }
    });
  }
});
