// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  expose: vi.fn(),
  installActivation: vi.fn(),
  sendSync: vi.fn(),
}));
vi.mock("electron", () => ({
  contextBridge: { exposeInMainWorld: mocks.expose },
  ipcRenderer: { sendSync: mocks.sendSync },
}));
vi.mock("@electron-toolkit/preload", () => ({ electronAPI: {} }));
vi.mock("./dictation-activation", () => ({
  installCodexDictationActivation: mocks.installActivation,
}));

describe("optional dictation preload admission", () => {
  const platform = Object.getOwnPropertyDescriptor(process, "platform")!;
  const isolated = Object.getOwnPropertyDescriptor(process, "contextIsolated");

  beforeEach(() => {
    vi.resetModules();
    vi.resetAllMocks();
    Object.defineProperty(process, "platform", { value: "win32", configurable: true });
    Object.defineProperty(process, "contextIsolated", { value: true, configurable: true });
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    Object.defineProperty(process, "platform", platform);
    if (isolated) Object.defineProperty(process, "contextIsolated", isolated);
    else Reflect.deleteProperty(process, "contextIsolated");
    vi.restoreAllMocks();
  });

  it("exposes core Desktop bridges before optional dictation initialization", async () => {
    await import("./index");
    expect(mocks.expose.mock.calls.map(([name]) => name)).toEqual([
      "electron", "desktopAPI", "daemonAPI", "updater",
    ]);
    expect(mocks.installActivation).toHaveBeenCalledOnce();
    expect(mocks.expose.mock.invocationCallOrder.at(-1))
      .toBeLessThan(mocks.installActivation.mock.invocationCallOrder[0]!);
  });

  it("isolates activation startup failure without exposing its private error", async () => {
    mocks.installActivation.mockImplementation(() => { throw new Error("private fixture error"); });
    await expect(import("./index")).resolves.toBeDefined();
    expect(mocks.expose.mock.calls.map(([name]) => name)).toEqual([
      "electron", "desktopAPI", "daemonAPI", "updater",
    ]);
    expect(console.warn).toHaveBeenCalledExactlyOnceWith("[dictation] activation_unavailable");
  });

  it("does not install Windows dictation admission on other platforms", async () => {
    Object.defineProperty(process, "platform", { value: "darwin", configurable: true });
    await import("./index");
    expect(mocks.expose).toHaveBeenCalledTimes(4);
    expect(mocks.installActivation).not.toHaveBeenCalled();
  });
});
