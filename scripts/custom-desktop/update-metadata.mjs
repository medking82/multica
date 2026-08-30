import assert from "node:assert/strict";
import { createRequire } from "node:module";

const desktopRequire = createRequire(new URL("../../apps/desktop/package.json", import.meta.url));
const coreRequire = createRequire(new URL("../../packages/core/package.json", import.meta.url));
const { parseUpdateInfo } = desktopRequire("electron-updater/out/providers/Provider.js");
const yaml = coreRequire("yaml");

/** Keep publication dependency-free, but validate the serialization with the
 * very parser installed clients use, not merely JSON.parse or a different YAML
 * package. Fail closed on type/field drift, including releaseDate and files. */
export function serializeUpdateMetadata(raw) {
  const info = yaml.parse(raw);
  const serialized = JSON.stringify(info, null, 2) + "\n";
  const url = "https://github.com/medking82/multica/releases/download/test/latest.yml";
  assert.deepEqual(parseUpdateInfo(serialized, "latest.yml", url), parseUpdateInfo(raw, "latest.yml", url),
    "Metadata serialization differs under electron-updater's own parser");
  return { info, serialized };
}
