import assert from "node:assert/strict";

// These are inclusion guards for deliberately unminified packaged output, not
// a substitute for the native Electron/Windows behavioral smoke tests.
export function assertDesktopSafetyContract({ main, preload, renderer }) {
  for (const marker of [
    "executeJavaScriptInIsolatedWorld(CODEX_DICTATION_WORLD",
    "multicaDictationActivation?.consume() === true",
    '"before-input-event"',
    'input.code === "KeyD"',
    'case "cleanup_failed":',
    '"updater:get-install-state"',
    "autoInstallOnAppQuit = false",
    '"will-quit"',
    "authorization.event.defaultPrevented",
  ]) {
    assert.ok(main.includes(marker), `Missing packaged main safety binding: ${marker}`);
  }
  for (const marker of [
    'exposeInIsolatedWorld(CODEX_DICTATION_WORLD, "multicaDictationActivation"',
    "!event.isTrusted",
    "button[data-native-dictation]",
    "installCodexDictationActivation();",
    "getInstallState:",
    "onInstallStateChanged:",
  ]) {
    assert.ok(preload.includes(marker), `Missing packaged preload safety binding: ${marker}`);
  }
  for (const marker of [
    "data-native-dictation",
    "window.updater.onInstallStateChanged(",
    "window.updater.getInstallState()",
    "Retry installation",
    "This check never stops agents.",
  ]) {
    assert.ok(renderer.includes(marker), `Missing packaged renderer safety binding: ${marker}`);
  }
}
