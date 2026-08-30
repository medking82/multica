import assert from "node:assert/strict";

export const FORK = "medking82/multica";
export const UPSTREAM = "multica-ai/multica";
export const BRANCH = "codex/desktop-custom";
export const WORKFLOW = "custom-desktop.yml";
export const WORKFLOW_NAME = "Custom Desktop Release";
export const CONTROLLER_FILES = [
  `.github/workflows/${WORKFLOW}`,
  "scripts/custom-desktop/release.mjs",
  "scripts/custom-desktop/release-policy.mjs",
];

export function sha(value) {
  assert.match(value ?? "", /^[a-f0-9]{40}$/, "Expected an immutable Git commit SHA");
  return value;
}

export function stableVersion(release) {
  assert.equal(release.draft, false, "Upstream release must be published");
  assert.equal(release.prerelease, false, "Upstream release must be stable");
  assert.match(release.tag_name ?? "", /^v\d+\.\d+\.\d+$/, "Unexpected upstream tag");
  return release.tag_name.slice(1);
}

export function customVersion(upstreamVersion, runNumber) {
  assert.match(upstreamVersion, /^\d+\.\d+\.\d+$/);
  assert.match(String(runNumber), /^[1-9]\d*$/);
  assert.ok(Number.isSafeInteger(Number(runNumber)));
  return `${upstreamVersion}-custom.${runNumber}`;
}

export function versionParts(version) {
  const match = /^(\d+)\.(\d+)\.(\d+)-custom\.([1-9]\d*)$/.exec(version ?? "");
  assert.ok(match, "Unexpected custom version");
  return match.slice(1).map(Number);
}

export function assertNewer(version, previous) {
  const left = versionParts(version);
  const right = versionParts(previous);
  const firstDifference = left.findIndex((part, i) => part !== right[i]);
  assert.ok(firstDifference >= 0 && left[firstDifference] > right[firstDifference],
    "Refusing a duplicate or downgraded update");
}

export function releaseNeeded({ base, released, upstreamIsAncestor }) {
  sha(base);
  if (released) sha(released);
  return base !== released || !upstreamIsAncestor;
}

export function assetNames(version) {
  versionParts(version);
  const installer = `multica-desktop-${version}-windows-x64.exe`;
  return [installer, `${installer}.blockmap`, "latest.yml"];
}

export function validatePlan(plan) {
  assert.equal(plan.format, 1);
  assert.equal(plan.repository, FORK);
  assert.equal(plan.branch, BRANCH);
  sha(plan.base);
  sha(plan.source);
  sha(plan.upstreamCommit);
  assert.match(plan.upstreamTag, /^v\d+\.\d+\.\d+$/);
  assert.match(String(plan.runId), /^[1-9]\d*$/);
  assert.equal(plan.version, customVersion(plan.upstreamTag.slice(1), plan.runNumber));
  assert.equal(plan.tag, `desktop-v${plan.version}`);
  return plan;
}

export function validateManifest(manifest) {
  validatePlan(manifest);
  assert.equal(manifest.platform, "windows-x64");
  assert.equal(manifest.signed, false);
  assert.deepEqual(manifest.features, ["workspace-skill-picker", "native-codex-dictation"]);
  const names = assetNames(manifest.version);
  assert.deepEqual(Object.keys(manifest.assets).sort(), [...names].sort(), "Unexpected release assets");
  for (const [name, record] of Object.entries(manifest.assets)) {
    assert.ok(Number.isSafeInteger(record.size) && record.size > 0);
    const cap = name.endsWith(".exe") ? 512 * 1024 * 1024 : name.endsWith(".blockmap") ? 16 * 1024 * 1024 : 64 * 1024;
    assert.ok(record.size <= cap, "Oversized release asset");
    assert.match(record.sha256 ?? "", /^[a-f0-9]{64}$/);
    assert.match(record.sha512 ?? "", /^[A-Za-z0-9+/]{86}==$/);
  }
  return manifest;
}

export function validateUpdateInfo(info, manifest) {
  const installer = assetNames(manifest.version)[0];
  const record = manifest.assets[installer];
  assert.equal(info.version, manifest.version);
  assert.equal(info.path, installer);
  assert.equal(info.sha512, record.sha512);
  assert.equal(info.files.length, 1);
  assert.equal(info.files[0].url, installer);
  assert.equal(info.files[0].sha512, record.sha512);
  assert.equal(info.files[0].size, record.size);
  assert.equal(info.packages, undefined, "Web installers are not supported");
  assert.notEqual(info.files[0].isAdminRightsRequired, true);
}
