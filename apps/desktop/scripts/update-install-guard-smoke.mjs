// node scripts/update-install-guard-smoke.mjs (Windows only)
// Execute the real fixed CIM probe against our own disposable executable.
import assert from "node:assert/strict";
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync } from "node:fs";
import { once } from "node:events";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import ts from "typescript";

assert.equal(process.platform, "win32", "this smoke requires Windows");
assert.equal(process.versions.electron, undefined, "run with Node, not Electron");
const require = createRequire(import.meta.url);
const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const fixture = mkdtempSync(join(tmpdir(), "multica-update-probe-"));
const binaryDir = join(fixture, "space ' & 界");
mkdirSync(binaryDir);
const cli = join(binaryDir, "multica.exe");
copyFileSync(process.execPath, cli);
let bundledPath = cli;
const module = { exports: {} };
const compiled = ts.transpileModule(readFileSync(join(root, "src/main/update-install-guard.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
new Function("require", "module", "exports", compiled)(
  (id) => id === "./bundled-cli" ? { bundledCliPath: () => bundledPath } : require(id), module, module.exports,
);
const { checkWindowsUpdateInstall } = module.exports;
let child;
let exited;
try {
  child = spawn(cli, ["-e", 'process.stdout.write("ready\\n"); process.stdin.resume()'], {
    windowsHide: true,
    stdio: ["pipe", "pipe", "pipe"],
    env: { SystemRoot: process.env.SystemRoot, TEMP: process.env.TEMP, TMP: process.env.TMP },
  });
  exited = once(child, "exit");
  await Promise.race([
    once(child.stdout, "data"),
    new Promise((_, reject) => {
      const timer = setTimeout(() => reject(new Error("fixture did not start")), 10000);
      timer.unref();
    }),
  ]);
  assert.deepEqual(await checkWindowsUpdateInstall(), { allowed: false, reason: "runtime_running" });
  bundledPath = join(fixture, "nonmatching", "multica.exe");
  assert.deepEqual(await checkWindowsUpdateInstall(), { allowed: true });
  child.stdin.end();
  const [code] = await exited;
  assert.equal(code, 0);
  bundledPath = cli;
  assert.deepEqual(await checkWindowsUpdateInstall(), { allowed: true });
  console.log(JSON.stringify({ passed: true, checks: ["running exact path blocks", "nonmatching path clears", "stopped fixture clears", "spaces/apostrophe/Unicode path"], installerRuns: 0, userDaemonsStopped: 0 }));
} finally {
  if (child && child.exitCode === null) { child.kill(); await exited; }
  // Only the directory minted above may be removed; never infer a user path.
  const target = realpathSync(fixture);
  assert.equal(dirname(target).toLowerCase(), realpathSync(tmpdir()).toLowerCase());
  assert.ok(basename(target).startsWith("multica-update-probe-"));
  rmSync(target, { recursive: true });
}
