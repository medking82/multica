import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { appendFile, mkdir, readFile, readdir, stat, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  FORK, UPSTREAM, BRANCH, WORKFLOW, WORKFLOW_NAME, CONTROLLER_FILES, sha, stableVersion,
  customVersion, assertNewer, releaseNeeded, assetNames, validatePlan,
  validateManifest, validateUpdateInfo,
} from "./release-policy.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");

function command(executable, args, options = {}) {
  const { trim = true, ...spawnOptions } = options;
  const result = spawnSync(executable, args, {
    cwd: root, encoding: "utf8", windowsHide: true, timeout: 600_000,
    maxBuffer: 16 * 1024 * 1024, ...spawnOptions,
  });
  if (result.error || result.status !== 0) {
    throw new Error(`${executable} ${args[0]} failed: ${result.error?.message ?? result.stderr?.trim() ?? result.status}`);
  }
  return trim ? result.stdout?.trim() ?? "" : result.stdout ?? "";
}

function git(...args) { return command("git", args); }

function api(method, path, body, missing = false) {
  const args = ["api", "--method", method, path];
  if (body !== undefined) args.push("--input", "-");
  try {
    const output = command("gh", args, { input: body === undefined ? undefined : JSON.stringify(body) });
    return output ? JSON.parse(output) : null;
  } catch (error) {
    if (missing && method === "GET" && error.message.includes("HTTP 404")) return null;
    throw error;
  }
}

function requireFork() {
  if (process.env.GITHUB_REPOSITORY) assert.equal(process.env.GITHUB_REPOSITORY, FORK);
  const remote = git("remote", "get-url", "origin");
  assert.ok([`https://github.com/${FORK}.git`, `https://github.com/${FORK}`, `git@github.com:${FORK}.git`].includes(remote),
    "Release must run against the explicitly authorized fork");
}

function head() { return sha(git("rev-parse", "HEAD")); }
function remoteHead() { return sha(api("GET", `repos/${FORK}/git/ref/heads/${BRANCH}`).object.sha); }

async function jsonFile(path) {
  const info = await stat(path);
  assert.ok(info.isFile() && info.size <= 64 * 1024, "Unexpected metadata file");
  return JSON.parse(await readFile(path, "utf8"));
}

export async function digest(path) {
  const sha256 = createHash("sha256");
  const sha512 = createHash("sha512");
  for await (const bytes of createReadStream(path)) { sha256.update(bytes); sha512.update(bytes); }
  return { size: (await stat(path)).size, sha256: sha256.digest("hex"), sha512: sha512.digest("base64") };
}

async function output(values) {
  if (process.env.GITHUB_OUTPUT) {
    await appendFile(process.env.GITHUB_OUTPUT, Object.entries(values).map(([key, value]) => `${key}=${value}\n`).join(""));
  }
  console.log(JSON.stringify(values));
}

function releasedCommit(release) {
  if (!release) return null;
  assert.equal(release.draft, false);
  assert.equal(release.prerelease, false);
  assert.match(release.tag_name, /^desktop-v\d+\.\d+\.\d+-custom\.[1-9]\d*$/);
  const ref = api("GET", `repos/${FORK}/git/ref/tags/${release.tag_name}`);
  assert.equal(ref.object.type, "commit", "Custom release tags must be lightweight immutable commits");
  return sha(ref.object.sha);
}

export function assertCandidateParents(source, base, upstreamCommit, parents) {
  sha(source); sha(base); sha(upstreamCommit);
  if (source === base) return;
  assert.deepEqual(parents, [base, upstreamCommit], "Candidate must be the exact tested two-parent upstream merge");
}

export function verifyCandidateTree({ source, base, upstreamCommit }, runGit = git) {
  sha(source); sha(base); sha(upstreamCommit);
  if (source === base) {
    runGit("merge-base", "--is-ancestor", upstreamCommit, base);
    return;
  }
  // Recompute from canonical, fetched parents without checking out or running
  // candidate code. A bundle with valid parents but an arbitrary tree fails.
  const expectedTree = sha(runGit("merge-tree", "--write-tree", base, upstreamCommit).split("\n")[0]);
  assert.equal(sha(runGit("show", "-s", "--format=%T", source)), expectedTree,
    "Candidate tree does not match the canonical upstream merge");
}

export function readControllerAtCommit(revision, path, cwd = root) {
  sha(revision);
  assert.ok(CONTROLLER_FILES.includes(path), "Unexpected controller path");
  // Never publish checkout bytes: the operator may edit that checkout while
  // the detached release worker runs. Preserve the committed blob verbatim.
  return Buffer.from(command("git", ["show", `${revision}:${path}`], { cwd, trim: false }), "utf8");
}

async function prepare(directory) {
  requireFork();
  assert.equal(process.env.GITHUB_ACTIONS, "true", "Candidate preparation is CI-only");
  assert.equal(git("status", "--porcelain"), "", "Candidate checkout must be clean");
  const base = head();
  assert.equal(base, remoteHead(), "Custom branch changed before preparation");
  const releases = api("GET", `repos/${FORK}/releases?per_page=100`);
  assert.ok(!releases.some((release) => release.draft && release.tag_name.startsWith("desktop-v")),
    "An unfinished custom draft requires explicit operator recovery");
  const upstream = api("GET", `repos/${UPSTREAM}/releases/latest`);
  const upstreamVersion = stableVersion(upstream);
  const upstreamCommit = sha(api("GET", `repos/${UPSTREAM}/commits/${upstream.tag_name}`).sha);
  // Fetch the resolved commit, never an unpinned moving branch or a force-updated local tag.
  git("fetch", "--no-tags", `https://github.com/${UPSTREAM}.git`, upstreamCommit);
  assert.equal(git("rev-parse", "FETCH_HEAD"), upstreamCommit);
  const ancestor = spawnSync("git", ["merge-base", "--is-ancestor", upstreamCommit, base], {
    cwd: root, windowsHide: true, timeout: 30_000,
  });
  assert.ok(ancestor.status === 0 || ancestor.status === 1, "Cannot inspect upstream ancestry");
  const latest = api("GET", `repos/${FORK}/releases/latest`, undefined, true);
  if (!releaseNeeded({ base, released: releasedCommit(latest), upstreamIsAncestor: ancestor.status === 0 })) {
    await output({ needed: false });
    return;
  }
  const version = customVersion(upstreamVersion, process.env.GITHUB_RUN_NUMBER);
  if (latest) assertNewer(version, latest.tag_name.slice("desktop-v".length));
  const tag = `desktop-v${version}`;
  assert.equal(api("GET", `repos/${FORK}/git/ref/tags/${tag}`, undefined, true), null, "Release tag already exists");
  if (ancestor.status !== 0) {
    command("git", ["-c", "commit.gpgsign=false", "merge", "--no-ff", "--no-edit", upstreamCommit], {
      env: {
        ...process.env, GIT_AUTHOR_NAME: "github-actions[bot]", GIT_COMMITTER_NAME: "github-actions[bot]",
        GIT_AUTHOR_EMAIL: "41898282+github-actions[bot]@users.noreply.github.com",
        GIT_COMMITTER_EMAIL: "41898282+github-actions[bot]@users.noreply.github.com",
      },
    });
  }
  const source = head();
  assertCandidateParents(source, base, upstreamCommit, git("show", "-s", "--format=%P", source).split(" "));
  await mkdir(directory, { recursive: true });
  assert.deepEqual(await readdir(directory), [], "Refusing to overwrite an existing payload");
  const plan = validatePlan({
    format: 1, repository: FORK, branch: BRANCH, base, source, upstreamCommit,
    upstreamTag: upstream.tag_name, version, tag,
    runId: process.env.GITHUB_RUN_ID, runNumber: process.env.GITHUB_RUN_NUMBER,
  });
  await writeFile(join(directory, "plan.json"), JSON.stringify(plan, null, 2) + "\n", { flag: "wx" });
  if (source !== base) {
    git("branch", "custom-desktop-candidate", source);
    git("bundle", "create", join(directory, "candidate.bundle"), "refs/heads/custom-desktop-candidate", `^${base}`);
  }
  await output({ needed: true, version, source, base });
}

async function publish(directory) {
  requireFork();
  assert.equal(process.env.GITHUB_ACTIONS, "true", "Publication is CI-only");
  const manifest = validateManifest(await jsonFile(join(directory, "custom-desktop.json")));
  assert.equal(manifest.runId, process.env.GITHUB_RUN_ID);
  assert.equal(manifest.runNumber, process.env.GITHUB_RUN_NUMBER);
  const expected = [...assetNames(manifest.version), "plan.json", "custom-desktop.json"];
  if (manifest.source !== manifest.base) expected.push("candidate.bundle");
  assert.deepEqual((await readdir(directory)).sort(), expected.sort(), "Unexpected payload content");
  assert.deepEqual(validatePlan(await jsonFile(join(directory, "plan.json"))),
    Object.fromEntries(Object.keys(await jsonFile(join(directory, "plan.json"))).map((key) => [key, manifest[key]])));
  for (const [name, record] of Object.entries(manifest.assets)) {
    assert.deepEqual(await digest(join(directory, name)), record, `Artifact checksum mismatch: ${name}`);
  }
  // JSON is a YAML subset. The build preserves electron-builder's update info
  // as canonical JSON so this privileged job needs no candidate dependencies.
  validateUpdateInfo(await jsonFile(join(directory, "latest.yml")), manifest);
  assert.equal(remoteHead(), manifest.base, "Custom branch advanced during the build");
  const upstream = api("GET", `repos/${UPSTREAM}/releases/latest`);
  assert.equal(stableVersion(upstream), manifest.upstreamTag.slice(1), "Upstream advanced during the build");
  assert.equal(sha(api("GET", `repos/${UPSTREAM}/commits/${manifest.upstreamTag}`).sha), manifest.upstreamCommit);
  const latest = api("GET", `repos/${FORK}/releases/latest`, undefined, true);
  if (latest) assertNewer(manifest.version, latest.tag_name.slice("desktop-v".length));
  assert.equal(api("GET", `repos/${FORK}/git/ref/tags/${manifest.tag}`, undefined, true), null);
  assert.equal(api("GET", `repos/${FORK}/releases/tags/${manifest.tag}`, undefined, true), null);
  git("fetch", "--no-tags", `https://github.com/${FORK}.git`, manifest.base);
  git("fetch", "--no-tags", `https://github.com/${UPSTREAM}.git`, manifest.upstreamCommit);
  if (manifest.source !== manifest.base) {
    git("bundle", "verify", join(directory, "candidate.bundle"));
    git("fetch", join(directory, "candidate.bundle"), "refs/heads/custom-desktop-candidate");
    assert.equal(git("rev-parse", "FETCH_HEAD"), manifest.source);
    assertCandidateParents(manifest.source, manifest.base, manifest.upstreamCommit,
      git("show", "-s", "--format=%P", manifest.source).split(" "));
  }
  verifyCandidateTree(manifest);
  if (manifest.source !== manifest.base) {
    assert.ok(process.env.GH_TOKEN, "Missing publication credential");
    // Per-process HTTPS credential, never persisted in the checkout or printed.
    command("git", ["push", "origin", `${manifest.source}:refs/heads/${BRANCH}`], {
      env: {
        ...process.env, GIT_CONFIG_COUNT: "1",
        GIT_CONFIG_KEY_0: "http.https://github.com/.extraheader",
        GIT_CONFIG_VALUE_0: `AUTHORIZATION: basic ${Buffer.from(`x-access-token:${process.env.GH_TOKEN}`).toString("base64")}`,
      },
    });
  }
  const release = api("POST", `repos/${FORK}/releases`, {
    tag_name: manifest.tag, target_commitish: manifest.source, draft: true, prerelease: false,
    name: `Multica Custom Desktop ${manifest.version}`,
    body: `Windows x64 custom build. UNSIGNED.\n\nIncludes Workspace Skill / picker and native Codex dictation (no Multica transcription API).\n\nSource: ${manifest.source}\nUpstream: ${manifest.upstreamTag} (${manifest.upstreamCommit})\nCI: https://github.com/${FORK}/actions/runs/${manifest.runId}\n\nInstall updates only after active agent runs have finished. Real audio recording requires a user smoke test.`,
  });
  const uploads = [...assetNames(manifest.version), "custom-desktop.json"];
  command("gh", ["release", "upload", manifest.tag, ...uploads.map((name) => join(directory, name)), "--repo", FORK]);
  const uploaded = api("GET", `repos/${FORK}/releases/${release.id}/assets?per_page=100`);
  assert.deepEqual(uploaded.map((asset) => asset.name).sort(), uploads.sort());
  for (const asset of uploaded) {
    const expectedDigest = await digest(join(directory, asset.name));
    assert.equal(asset.state, "uploaded");
    assert.equal(asset.size, expectedDigest.size);
    assert.equal(asset.digest, `sha256:${expectedDigest.sha256}`, "GitHub did not confirm the uploaded digest");
  }
  // The feed switches only after every upload and digest check has succeeded.
  const published = api("PATCH", `repos/${FORK}/releases/${release.id}`, { draft: false, prerelease: false, make_latest: "true" });
  assert.equal(published.draft, false);
  console.log(published.html_url);
}

async function bootstrapController() {
  requireFork();
  const releaseCommit = sha(process.env.SOP_RELEASE_COMMIT);
  assert.equal(head(), releaseCommit);
  assert.equal(remoteHead(), releaseCommit);
  const main = api("GET", `repos/${FORK}/git/ref/heads/main`).object.sha;
  const commit = api("GET", `repos/${FORK}/git/commits/${sha(main)}`);
  const missing = [];
  for (const path of CONTROLLER_FILES) {
    const bytes = readControllerAtCommit(releaseCommit, path);
    const existing = api("GET", `repos/${FORK}/contents/${path}?ref=${main}`, undefined, true);
    if (existing) {
      assert.equal(existing.type, "file");
      assert.ok(bytes.equals(Buffer.from(existing.content, "base64")),
        `Controller file ${path} differs on main; explicit controller migration is required`);
      continue;
    }
    missing.push({ path, bytes });
  }
  if (!missing.length) return;
  // Complete the read-only admission before creating any GitHub object.
  const entries = missing.map(({ path, bytes }) => {
    const blob = api("POST", `repos/${FORK}/git/blobs`, { content: bytes.toString("base64"), encoding: "base64" });
    return { path, mode: "100644", type: "blob", sha: blob.sha };
  });
  const tree = api("POST", `repos/${FORK}/git/trees`, { base_tree: commit.tree.sha, tree: entries });
  const next = api("POST", `repos/${FORK}/git/commits`, {
    message: "ci(desktop): enable verified custom update controller", tree: tree.sha, parents: [main],
  });
  // A concurrent upstream sync causes a safe non-fast-forward failure, not a retry.
  api("PATCH", `repos/${FORK}/git/refs/heads/main`, { sha: next.sha, force: false });
  console.log(`Installed custom update controller at ${next.sha}`);
}

async function dispatch() {
  requireFork();
  const source = sha(process.env.SOP_RELEASE_COMMIT);
  assert.equal(head(), source);
  assert.equal(remoteHead(), source);
  api("POST", `repos/${FORK}/actions/workflows/${WORKFLOW}/dispatches`, { ref: BRANCH });
  console.log(`Dispatched ${WORKFLOW} at ${source}`);
}

async function remoteVerify() {
  requireFork();
  const source = sha(process.env.SOP_RELEASE_COMMIT);
  const latest = api("GET", `repos/${FORK}/releases/latest`);
  const tagCommit = releasedCommit(latest);
  const asset = latest.assets.find((entry) => entry.name === "custom-desktop.json");
  assert.ok(asset, "Missing published manifest");
  const response = await fetch(asset.browser_download_url, { signal: AbortSignal.timeout(30_000) });
  assert.ok(response.ok);
  const bytes = Buffer.from(await response.arrayBuffer());
  assert.ok(bytes.length <= 64 * 1024);
  assert.equal(`sha256:${createHash("sha256").update(bytes).digest("hex")}`, asset.digest);
  const manifest = validateManifest(JSON.parse(bytes.toString("utf8")));
  assert.equal(manifest.source, tagCommit);
  assert.ok(manifest.base === source || manifest.source === source, "Published build is not bound to the SOP commit");
  for (const [name, record] of Object.entries(manifest.assets)) {
    const releasedAsset = latest.assets.find((entry) => entry.name === name);
    assert.equal(releasedAsset?.digest, `sha256:${record.sha256}`);
    assert.equal(releasedAsset?.size, record.size);
  }
  console.log(JSON.stringify({ verified: true, version: manifest.version, source: manifest.source, url: latest.html_url }));
}

function watchCI() {
  requireFork();
  const source = sha(process.env.SOP_RELEASE_COMMIT);
  assert.equal(head(), source);
  // Delegate all polling and durable failure handling to the installed SOP.
  command("python", [".sop/sop.py", "--repo", ".", "actions-watch", "--commit", source,
    "--workflow", WORKFLOW_NAME, "--discover-timeout", "120", "--timeout", "3600"],
  { stdio: "inherit", timeout: 3_700_000, env: { ...process.env, GH_REPO: FORK } });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [mode, directory] = process.argv.slice(2);
  const actions = { prepare, publish, bootstrap: bootstrapController, dispatch, watch: watchCI, verify: remoteVerify };
  assert.ok(actions[mode], "Unknown release operation");
  if (mode === "prepare" || mode === "publish") assert.ok(directory, "An explicit payload directory is required");
  await actions[mode](directory && resolve(directory));
}
