import assert from "node:assert/strict";
import { test } from "node:test";
import { assertDesktopSafetyContract } from "./desktop-safety-contract.mjs";

const packaged = {
  main: `
    sender.executeJavaScriptInIsolatedWorld(CODEX_DICTATION_WORLD, [{
      code: "globalThis.multicaDictationActivation?.consume() === true"
    }]);
    window.webContents.on("before-input-event", (_, input) => input.code === "KeyD");
    switch (result) { case "cleanup_failed": break; }
    ipcMain.handle("updater:get-install-state", () => state);
    autoUpdater.autoInstallOnAppQuit = false;
    app.on("will-quit", captureFinalQuit);
    app.on("quit", () => { if (authorization.event.defaultPrevented) return; });
  `,
  preload: `
    if (!event.isTrusted) return;
    target.closest("button[data-native-dictation]");
    contextBridge.exposeInIsolatedWorld(CODEX_DICTATION_WORLD, "multicaDictationActivation", bridge);
    installCodexDictationActivation();
    const updater = { getInstallState: snapshot, onInstallStateChanged: subscribe };
  `,
  renderer: `
    const mic = { "data-native-dictation": "" };
    window.updater.onInstallStateChanged(receive);
    window.updater.getInstallState();
    "Retry installation";
    "This check never stops agents.";
  `,
};

test("packaged custom build includes reviewed dictation and updater bindings", () => {
  assertDesktopSafetyContract(packaged);
});

for (const [part, marker] of [
  ["main", "executeJavaScriptInIsolatedWorld(CODEX_DICTATION_WORLD"],
  ["main", "multicaDictationActivation?.consume() === true"],
  ["main", '"before-input-event"'],
  ["main", 'input.code === "KeyD"'],
  ["main", 'case "cleanup_failed":'],
  ["main", '"updater:get-install-state"'],
  ["main", "autoInstallOnAppQuit = false"],
  ["main", '"will-quit"'],
  ["main", "authorization.event.defaultPrevented"],
  ["preload", 'exposeInIsolatedWorld(CODEX_DICTATION_WORLD, "multicaDictationActivation"'],
  ["preload", "!event.isTrusted"],
  ["preload", "button[data-native-dictation]"],
  ["preload", "installCodexDictationActivation();"],
  ["preload", "getInstallState:"],
  ["preload", "onInstallStateChanged:"],
  ["renderer", "window.updater.onInstallStateChanged("],
  ["renderer", "data-native-dictation"],
  ["renderer", "window.updater.getInstallState()"],
  ["renderer", "Retry installation"],
  ["renderer", "This check never stops agents."],
]) {
  test(`packaged guard rejects missing ${part} binding: ${marker}`, () => {
    assert.throws(() => assertDesktopSafetyContract({
      ...packaged,
      [part]: packaged[part].replace(marker, "REMOVED"),
    }), /Missing packaged/);
  });
}
