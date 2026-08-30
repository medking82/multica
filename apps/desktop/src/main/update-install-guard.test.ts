// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ execFile: vi.fn() }));
vi.mock("node:child_process", () => ({ execFile: mocks.execFile }));
vi.mock("./bundled-cli", () => ({ bundledCliPath: () => "C:\\Multica\\resources\\bin\\multica.exe" }));
import { canInstallWindowsUpdate } from "./update-install-guard";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs(); mocks.execFile.mockReset(); });

describe("Windows installer process guard", () => {
  it.each([
    [null, "clear\r\n", true],
    [null, "blocked\r\n", false],
    [null, "unexpected", false],
    [new Error("timed out"), "clear", false],
  ])("fails closed on busy, unknown, or failed probes", async (error, output, expected) => {
    vi.stubEnv("SystemRoot", "C:\\Windows");
    mocks.execFile.mockImplementation((_exe, _args, _opts, callback) => callback(error, output));
    await expect(canInstallWindowsUpdate("win32")).resolves.toBe(expected);
    const [exe, args, options] = mocks.execFile.mock.calls[0]!;
    expect(exe).toContain("WindowsPowerShell");
    expect(args).toContain("-NonInteractive");
    expect(args.at(-1)).toContain("Get-CimInstance Win32_Process");
    expect(args.at(-1)).not.toMatch(/Stop-Process|Invoke-Expression|taskkill/i);
    expect(options).toMatchObject({ windowsHide: true, timeout: 6_000, env: { MULTICA_UPDATE_CLI_PATH: "C:\\Multica\\resources\\bin\\multica.exe" } });
  });
});
