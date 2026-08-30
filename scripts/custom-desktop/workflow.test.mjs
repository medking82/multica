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
  assert.ok(!publish.steps.some((step) => /pnpm|npm install|candidate\.bundle\//.test(step.run ?? "")));
  assert.match(publish.steps.at(-1).run, /release.mjs publish/);
});
