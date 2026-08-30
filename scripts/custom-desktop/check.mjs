import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { assertComposerContract, COMPOSERS } from "./composer-contract.mjs";

const root = fileURLToPath(new URL("../../", import.meta.url));
function run(exe, args, { cwd = root, capture = false, env = process.env } = {}) {
  console.log(`CHECK ${exe} ${args.join(" ")}`);
  const result = spawnSync(exe, args, {
    cwd, env, windowsHide: true, timeout: 1_800_000, encoding: "utf8",
    stdio: capture ? "pipe" : "inherit",
    // Only fixed pnpm arguments from this script cross the Windows .cmd shim.
    shell: process.platform === "win32" && exe === "pnpm",
  });
  if (result.error || result.status !== 0) throw new Error(`${exe} failed: ${result.error?.message ?? result.stderr ?? result.status}`);
  return result.stdout?.trim();
}

async function sourceContract() {
  for (const [name, path] of Object.entries(COMPOSERS)) {
    assertComposerContract(await readFile(join(root, path), "utf8"), [name], true);
  }
  for (const path of ["voice-input-button.tsx", "native-dictation-button.tsx"]) {
    const text = await readFile(join(root, "packages/views/editor", path), "utf8");
    assert.doesNotMatch(text, /MediaRecorder|getUserMedia|transcribeAudio|useConfigStore|@multica\/core\/api/);
  }
}

async function preflight() {
  assert.equal(process.platform, "win32", "Custom Desktop validation owns the Windows checkout");
  assert.equal(process.arch, "x64");
  assert.equal(run("pnpm", ["--version"], { capture: true }), "10.28.2", "Use the repository-pinned pnpm");
  assert.match(run("go", ["version"], { capture: true }), /^go version go1\.26\./);
  await sourceContract();
}

const mode = process.argv[2];
assert.ok(["preflight", "quick", "full"].includes(mode));
await preflight();
if (mode !== "preflight") {
  run("git", ["diff", "--check"]);
  run(process.execPath, ["--test", "scripts/custom-desktop/release-policy.test.mjs", "scripts/custom-desktop/release.test.mjs", "scripts/custom-desktop/composer-contract.test.mjs", "scripts/custom-desktop/desktop-safety-contract.test.mjs", "scripts/custom-desktop/workflow.test.mjs", "scripts/custom-desktop/update-metadata.test.mjs", "scripts/custom-desktop/bundle-cli.test.mjs"]);
  run("pnpm", ["--filter", "@multica/views", "test", "editor/voice-input-button.test.tsx", "editor/content-editor.test.tsx", "editor/extensions/slash-command-extension.test.ts", "editor/extensions/slash-command-suggestion.test.tsx", "modals/create-issue.test.tsx", "modals/quick-create-issue.test.tsx", "chat/components/chat-input.test.tsx", "issues/components/comment-composers.test.tsx", "issues/components/comment-dictation.test.tsx"]);
  run("pnpm", ["--filter", "@multica/desktop", "test", "src/main/codex-dictation.test.ts", "src/preload/dictation-activation.test.ts", "src/preload/index.test.ts", "src/renderer/src/platform/dictation-adapter.test.ts", "src/main/updater.test.ts", "src/main/update-install-guard.test.ts", "src/renderer/src/components/update-notification.test.tsx", "scripts/package.test.mjs"]);
  const caches = JSON.parse(run("go", ["env", "-json", "GOCACHE", "GOMODCACHE"], { capture: true }));
  const isolatedHome = await mkdtemp(join(tmpdir(), "multica-custom-tests-"));
  const env = { ...process.env, ...caches, HOME: isolatedHome, USERPROFILE: isolatedHome };
  const cwd = join(root, "server");
  run("go", ["test", "./internal/desktopdictation", "-count=1"], { cwd, env });
  run("go", ["test", "./cmd/multica", "-run", "^TestDesktopDictationHelperRunsBeforeNormalCLI$", "-count=1"], { cwd, env });
  run("go", ["vet", "./internal/desktopdictation"], { cwd, env });
}
if (mode === "full") {
  run("pnpm", ["--filter", "@multica/core", "--filter", "@multica/views", "--filter", "@multica/desktop", "typecheck"]);
  run("pnpm", ["--filter", "@multica/desktop", "test"]);
  run("pnpm", ["--filter", "@multica/views", "test", "editor", "modals/create-issue", "modals/quick-create-issue", "chat/components/chat-input", "issues/components"]);
}
console.log(`PASS custom Desktop ${mode}`);
