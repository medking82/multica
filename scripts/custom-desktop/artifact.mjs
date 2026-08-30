import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { copyFile, open, readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { assertComposerContract } from "./composer-contract.mjs";
import { serializeUpdateMetadata } from "./update-metadata.mjs";
import { digest } from "./release.mjs";
import { assetNames, validatePlan, validateManifest, validateUpdateInfo, validateCliVersionOutput } from "./release-policy.mjs";

const root = fileURLToPath(new URL("../../", import.meta.url));
const desktopRequire = createRequire(new URL("../../apps/desktop/package.json", import.meta.url));
const builderRequire = createRequire(desktopRequire.resolve("electron-builder/package.json"));
const libraryRequire = createRequire(builderRequire.resolve("app-builder-lib/package.json"));
const asar = libraryRequire("@electron/asar");
const coreRequire = createRequire(new URL("../../packages/core/package.json", import.meta.url));
const yaml = coreRequire("yaml");

async function peMachine(path) {
  const file = await open(path, "r");
  try {
    const header = Buffer.alloc(64);
    await file.read(header, 0, header.length, 0);
    assert.equal(header.toString("ascii", 0, 2), "MZ");
    const pe = Buffer.alloc(6);
    await file.read(pe, 0, pe.length, header.readUInt32LE(60));
    assert.equal(pe.toString("ascii", 0, 4), "PE\0\0");
    return pe.readUInt16LE(4);
  } finally { await file.close(); }
}

export async function inspectArtifact(directory, version) {
  const unpacked = join(directory, "win-unpacked");
  const archive = join(unpacked, "resources/app.asar");
  const cli = join(unpacked, "resources/app.asar.unpacked/resources/bin/multica.exe");
  const read = (path) => asar.extractFile(archive, normalize(path)).toString("utf8");
  assert.equal(JSON.parse(read("package.json")).version, version);
  const main = read("out/main/index.js");
  const preload = read("out/preload/index.js");
  assert.ok(main.includes("--desktop-dictation-v1") && main.includes("globalDictationToggle"));
  assert.ok(preload.includes("dictation:toggle-codex"));
  assert.ok(main.includes('owner: "medking82"') && main.includes("allowPrerelease = false") && main.includes("allowDowngrade = false"));
  const rendererPath = read("out/renderer/index.html").match(/src="\.\/([^\"]+\.js)"/)?.[1];
  assert.ok(rendererPath, "Missing renderer entry");
  const renderer = read(`out/renderer/${rendererPath}`);
  assert.ok(renderer.includes("function workspaceSkillItems("), "Missing Workspace Skill library");
  assert.ok(renderer.includes("qc.fetchQuery(skillListOptions(wsId))"), "Missing Workspace Skill fetch");
  assert.match(renderer, /item\.skillId\s*\?\?\s*item\.id/);
  assert.ok(renderer.includes("focusForNativeInput") && renderer.includes("Dictate with Codex"));
  assert.ok(renderer.includes("desktopDictationAdapter") && renderer.includes("dictationAdapter:"));
  for (const banned of ["audioTranscriptionEnabled", "transcribeAudio", "/audio/transcriptions"]) {
    assert.ok(!renderer.includes(banned), `Unapproved transcription draft found: ${banned}`);
  }
  assertComposerContract(renderer);
  const feed = yaml.parse(await readFile(join(unpacked, "resources/app-update.yml"), "utf8"));
  assert.equal(feed.provider, "github");
  assert.equal(feed.owner, "medking82");
  assert.equal(feed.repo, "multica");
  assert.equal(feed.channel, "latest");
  // Unsigned artifacts have no publisher identity to check. Do not turn off
  // Windows protections or configure a fake publisher to mask that limitation.
  assert.equal(feed.publisherName, undefined);
  assert.equal(asar.statFile(archive, normalize("resources/bin/multica.exe")).unpacked, true);
  assert.equal(await peMachine(join(unpacked, "Multica.exe")), 0x8664);
  assert.equal(await peMachine(cli), 0x8664);
  // Execute only the read-only version flag in the unprivileged Windows build
  // job. The publication controller validates the recorded result, never runs it.
  const cliVersion = validateCliVersionOutput(execFileSync(cli, ["--version"], {
    encoding: "utf8", windowsHide: true, timeout: 15_000,
    stdio: ["ignore", "pipe", "pipe"],
  }), version);
  const cliDigest = await digest(cli);
  assert.equal(cliDigest.sha256, (await digest(join(root, "apps/desktop/resources/bin/multica.exe"))).sha256);
  return { version, cliVersion, asarSHA256: (await digest(archive)).sha256, cliSHA256: cliDigest.sha256, packagedComposers: 5 };
}

const [mode, input, version] = process.argv.slice(2);
if (mode === "inspect") {
  console.log(JSON.stringify(await inspectArtifact(resolve(input), version), null, 2));
} else if (mode === "payload") {
  const directory = resolve(input);
  const plan = validatePlan(JSON.parse(await readFile(join(directory, "plan.json"), "utf8")));
  const build = join(root, "apps/desktop/dist");
  const inspection = await inspectArtifact(build, plan.version);
  const { info, serialized } = serializeUpdateMetadata(await readFile(join(build, "latest.yml"), "utf8"));
  const assets = {};
  for (const name of assetNames(plan.version)) {
    if (name === "latest.yml") {
      await writeFile(join(directory, name), serialized, { flag: "wx" });
    } else {
      await copyFile(join(build, name), join(directory, name));
    }
    assets[name] = await digest(join(directory, name));
  }
  const manifest = validateManifest({
    ...plan, platform: "windows-x64", signed: false,
    features: ["workspace-skill-picker", "native-codex-dictation"], assets, inspection,
  });
  validateUpdateInfo(info, manifest);
  await writeFile(join(directory, "custom-desktop.json"), JSON.stringify(manifest, null, 2) + "\n", { flag: "wx" });
  console.log(JSON.stringify(inspection));
} else {
  throw new Error("Use inspect <dist> <version> or payload <directory>");
}
