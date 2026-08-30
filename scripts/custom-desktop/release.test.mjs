import assert from "node:assert/strict";
import { test } from "node:test";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { assertCandidateParents, readControllerAtCommit, verifyCandidateTree } from "./release.mjs";

function fixture(t) {
  const cwd = mkdtempSync(join(tmpdir(), "multica-release-policy-"));
  t.after(() => rmSync(cwd, { recursive: true, force: true }));
  const git = (...args) => {
    const result = spawnSync("git", ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", ...args], {
      cwd, windowsHide: true, encoding: "utf8", timeout: 10_000,
      env: { ...process.env, GIT_CONFIG_NOSYSTEM: "1", GIT_CONFIG_GLOBAL: join(cwd, "missing-gitconfig") },
    });
    assert.equal(result.status, 0, result.stderr);
    return result.stdout.trim();
  };
  const write = (path, bytes) => {
    mkdirSync(dirname(join(cwd, path)), { recursive: true });
    writeFileSync(join(cwd, path), bytes);
  };
  git("init", "-q");
  return { cwd, git, write };
}

test("publisher accepts only the tested source or its exact two-parent upstream merge", () => {
  const base = "a".repeat(40);
  const upstream = "b".repeat(40);
  const merged = "c".repeat(40);
  assertCandidateParents(base, base, upstream, []);
  assertCandidateParents(merged, base, upstream, [base, upstream]);
  for (const parents of [[], [base], [upstream, base], [base, upstream, merged], [base, merged]]) {
    assert.throws(() => assertCandidateParents(merged, base, upstream, parents));
  }
});

test("controller bootstrap reads the reviewed blob even when checkout bytes are dirty", (t) => {
  const { cwd, git, write } = fixture(t);
  const path = "scripts/custom-desktop/release.mjs";
  write(path, "// reviewed controller\n\n");
  git("add", "."); git("commit", "-qm", "reviewed");
  const revision = git("rev-parse", "HEAD");
  write(path, "// unreviewed local edit\n");
  assert.ok(git("status", "--porcelain").includes("release.mjs"));
  assert.equal(readControllerAtCommit(revision, path, cwd).toString("utf8"), "// reviewed controller\n\n");
  assert.throws(() => readControllerAtCommit(revision, "README.md", cwd));
});

test("valid merge parents cannot smuggle an arbitrary candidate tree", (t) => {
  const { git, write } = fixture(t);
  write("base.txt", "base\n"); git("add", "."); git("commit", "-qm", "base");
  const common = git("rev-parse", "HEAD");
  git("checkout", "-qb", "upstream");
  write("upstream.txt", "official\n"); git("add", "."); git("commit", "-qm", "official");
  const upstreamCommit = git("rev-parse", "HEAD");
  git("checkout", "-qb", "custom", common);
  write("custom.txt", "patches\n"); git("add", "."); git("commit", "-qm", "custom");
  const base = git("rev-parse", "HEAD");
  const expectedTree = git("merge-tree", "--write-tree", base, upstreamCommit);
  const source = git("commit-tree", expectedTree, "-p", base, "-p", upstreamCommit, "-m", "tested merge");
  verifyCandidateTree({ base, source, upstreamCommit }, git);
  const tampered = git("commit-tree", git("show", "-s", "--format=%T", base), "-p", base, "-p", upstreamCommit, "-m", "tampered tree");
  assertCandidateParents(tampered, base, upstreamCommit, git("show", "-s", "--format=%P", tampered).split(" "));
  assert.throws(() => verifyCandidateTree({ base, source: tampered, upstreamCommit }, git), /canonical upstream merge/);
  assert.throws(() => verifyCandidateTree({ base, source: base, upstreamCommit }, git));
  verifyCandidateTree({ base: source, source, upstreamCommit }, git);
});
