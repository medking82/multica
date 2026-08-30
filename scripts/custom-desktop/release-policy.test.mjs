import { test } from "node:test";
import assert from "node:assert/strict";
import {
  FORK, BRANCH, stableVersion, customVersion, assertNewer, releaseNeeded,
  assetNames, validatePlan, validateManifest, validateUpdateInfo,
} from "./release-policy.mjs";

const hash = "a".repeat(40);
const plan = {
  format: 1, repository: FORK, branch: BRANCH, base: hash, source: hash,
  upstreamCommit: "b".repeat(40), upstreamTag: "v0.4.36", runNumber: "12",
  runId: "100", version: "0.4.36-custom.12", tag: "desktop-v0.4.36-custom.12",
};
const record = { size: 100, sha256: "a".repeat(64), sha512: Buffer.alloc(64).toString("base64") };
const manifest = {
  ...plan, platform: "windows-x64", signed: false,
  features: ["workspace-skill-picker", "native-codex-dictation"],
  assets: Object.fromEntries(assetNames(plan.version).map((name) => [name, { ...record }])),
};

test("stable releases and monotonic custom versions", () => {
  assert.equal(stableVersion({ tag_name: "v0.4.36", draft: false, prerelease: false }), "0.4.36");
  assert.equal(customVersion("0.4.36", "12"), plan.version);
  assertNewer("0.4.36-custom.12", "0.4.36-custom.9");
  assertNewer("0.4.37-custom.1", "0.4.36-custom.99");
  assert.throws(() => assertNewer(plan.version, plan.version));
  assert.throws(() => assertNewer("0.4.36-custom.9", plan.version));
  assert.throws(() => assertNewer("0.4.35-custom.99", plan.version));
  for (const tag of ["main", "v1.2.3-beta", "v1.2.3\n", "v1.2.3;echo x"]) {
    assert.throws(() => stableVersion({ tag_name: tag, draft: false, prerelease: false }));
  }
  for (const flags of [{ draft: true, prerelease: false }, { draft: false, prerelease: true }]) {
    assert.throws(() => stableVersion({ ...flags, tag_name: "v0.4.36" }));
  }
  for (const run of [0, "01", "-1", "1;echo", "1.1", "9007199254740992"]) {
    assert.throws(() => customVersion("0.4.36", run));
  }
});

test("no-op only when both custom source and upstream are already released", () => {
  assert.equal(releaseNeeded({ base: hash, released: hash, upstreamIsAncestor: true }), false);
  assert.equal(releaseNeeded({ base: hash, released: null, upstreamIsAncestor: true }), true);
  assert.equal(releaseNeeded({ base: hash, released: hash, upstreamIsAncestor: false }), true);
  assert.equal(releaseNeeded({ base: hash, released: "b".repeat(40), upstreamIsAncestor: true }), true);
});

test("manifest closes repository, architecture, version, and asset boundaries", () => {
  validateManifest(manifest);
  for (const overrides of [
    { repository: "multica-ai/multica" }, { branch: "main" }, { source: "main" },
    { source: `${hash}\n` }, { tag: "v0.4.36" }, { runNumber: "13" },
    { version: "0.4.36" }, { runId: "bad" },
  ]) assert.throws(() => validatePlan({ ...plan, ...overrides }));
  for (const overrides of [
    { platform: "windows-arm64" }, { signed: true }, { features: ["native-codex-dictation"] },
    { assets: { ...manifest.assets, "../private.json": record } },
    { assets: Object.fromEntries(assetNames(plan.version).map((name) => [name, { ...record, size: 0 }])) },
  ]) assert.throws(() => validateManifest({ ...manifest, ...overrides }));
});

test("update metadata must describe exactly the verified installer", () => {
  const installer = assetNames(plan.version)[0];
  const info = {
    version: plan.version, path: installer, sha512: record.sha512,
    files: [{ url: installer, sha512: record.sha512, size: record.size }],
  };
  validateUpdateInfo(info, manifest);
  for (const overrides of [
    { version: "0.4.37" }, { path: "https://example.com/update.exe" },
    { sha512: "wrong" }, { packages: {} }, { files: [] },
    { files: [{ ...info.files[0], url: "../other.exe" }] },
    { files: [{ ...info.files[0], isAdminRightsRequired: true }] },
  ]) assert.throws(() => validateUpdateInfo({ ...info, ...overrides }, manifest));
});
