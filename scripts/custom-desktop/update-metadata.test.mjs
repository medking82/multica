import assert from "node:assert/strict";
import { test } from "node:test";
import { createRequire } from "node:module";
import { serializeUpdateMetadata } from "./update-metadata.mjs";

const desktopRequire = createRequire(new URL("../../apps/desktop/package.json", import.meta.url));
const { GitHubProvider } = desktopRequire("electron-updater/out/providers/GitHubProvider.js");
const version = "0.4.36-custom.2";
const tag = `desktop-v${version}`;
const installer = `multica-desktop-${version}-windows-x64.exe`;
const raw = `version: ${version}
files:
  - url: ${installer}
    sha512: ${"a".repeat(86)}==
    size: 12345
path: ${installer}
sha512: ${"a".repeat(86)}==
releaseDate: '2026-08-30T00:00:00.000Z'
`;

test("actual electron-updater YAML parser round-trips every emitted metadata field", () => {
  const { info, serialized } = serializeUpdateMetadata(raw);
  assert.deepEqual(JSON.parse(serialized), info);
  assert.equal(info.releaseDate, "2026-08-30T00:00:00.000Z");
  // Unquoted timestamps parse differently across YAML libraries. Do not ship
  // silently if a future builder changes the representation.
  assert.throws(() => serializeUpdateMetadata(raw.replace(/'2026-08-30T00:00:00.000Z'/, "2026-08-30T00:00:00.000Z")));
});

test("actual GitHubProvider resolves the custom tag and latest metadata without prerelease fallback", async () => {
  const requests = [];
  const prefix = "/medking82/multica/releases";
  const provider = new GitHubProvider({ owner: "medking82", repo: "multica", channel: "latest" },
    { allowPrerelease: false, channel: "latest", currentVersion: "0.4.36-custom.1", fullChangelog: false },
    { platform: "win32", isUseMultipleRangeRequest: false, executor: { async request(options) {
      assert.equal(options.hostname, "github.com");
      requests.push(options.path);
      if (options.path === `${prefix}.atom`) return `<feed><entry><title>Custom</title><link href="https://github.com${prefix}/tag/${tag}"/><content>Verified build</content></entry></feed>`;
      if (options.path === `${prefix}/latest`) return JSON.stringify({ tag_name: tag });
      if (options.path === `${prefix}/download/${tag}/latest.yml`) return serializeUpdateMetadata(raw).serialized;
      throw new Error(`Unexpected updater request: ${options.path}`);
    } } });
  const info = await provider.getLatestVersion();
  assert.equal(info.version, version);
  assert.equal(info.tag, tag);
  assert.equal(provider.resolveFiles(info)[0].url.href, `https://github.com${prefix}/download/${tag}/${installer}`);
  assert.deepEqual(requests, [`${prefix}.atom`, `${prefix}/latest`, `${prefix}/download/${tag}/latest.yml`]);
});
