import assert from "node:assert/strict";
import { test } from "node:test";
import { readFile } from "node:fs/promises";
import { assertComposerContract, COMPOSERS } from "./composer-contract.mjs";

test("all five source composers wire slash and native mic to the same editor", async () => {
  for (const [name, path] of Object.entries(COMPOSERS)) {
    assertComposerContract(await readFile(new URL(`../../${path}`, import.meta.url), "utf8"), [name], true);
  }
});

test("packaged guard rejects the previous missing-creation-picker regression", () => {
  const good = `function ManualCreatePanel() {
    jsx(ContentEditor, { ref: descEditorRef, enableSlashCommands: true });
    jsx(VoiceInputButton, { editorRef: descEditorRef });
  }`;
  assertComposerContract(good, ["ManualCreatePanel"]);
  assert.throws(() => assertComposerContract(good.replace("enableSlashCommands: true", "enableSlashCommands: false"), ["ManualCreatePanel"]));
  assert.throws(() => assertComposerContract(good.replace("editorRef: descEditorRef", "editorRef: otherRef"), ["ManualCreatePanel"]));
  assert.throws(() => assertComposerContract(good.replace("VoiceInputButton", "OtherButton"), ["ManualCreatePanel"]));
});

test("packaged React property shorthand still proves the same editor binding", () => {
  const emitted = `function ChatInput() {
    (0, jsxRuntime.jsx)(ContentEditor, { ref: editorRef, enableSlashCommands: true });
    (0, jsxRuntime.jsx)(VoiceInputButton, { editorRef });
  }`;
  assertComposerContract(emitted, ["ChatInput"]);
  assert.throws(() => assertComposerContract(emitted.replace("ref: editorRef", "ref: otherRef"), ["ChatInput"]));
  assert.throws(() => assertComposerContract(emitted.replace("enableSlashCommands: true", "enableSlashCommands: false"), ["ChatInput"]));
});
