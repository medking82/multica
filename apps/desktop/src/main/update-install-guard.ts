import { execFile } from "node:child_process";
import { join } from "node:path";
import { bundledCliPath } from "./bundled-cli";

// A running bundled CLI holds its executable open on Windows. Never stop it
// for an update: it may own active runs, even when Desktop is closing. Query
// only process metadata; no credentials, process termination, or user scripts.
const PROCESS_CHECK = [
  "$ErrorActionPreference = 'Stop'",
  "$cliPath = [IO.Path]::GetFullPath($env:MULTICA_UPDATE_CLI_PATH)",
  "$blocked = @(Get-CimInstance Win32_Process -Filter \"Name = 'multica.exe'\" | Where-Object {",
  "  -not $_.ExecutablePath -or [IO.Path]::GetFullPath($_.ExecutablePath) -ieq $cliPath",
  "}).Count -gt 0",
  "if ($blocked) { 'blocked' } else { 'clear' }",
].join("\n");

export async function canInstallWindowsUpdate(platform = process.platform): Promise<boolean> {
  if (platform !== "win32") return true;
  if (!process.env.SystemRoot) return false;
  return new Promise((resolve) => {
    execFile(
      join(process.env.SystemRoot!, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"),
      ["-NoLogo", "-NoProfile", "-NonInteractive", "-Command", PROCESS_CHECK],
      {
        windowsHide: true,
        timeout: 6_000,
        maxBuffer: 4_096,
        env: { ...process.env, MULTICA_UPDATE_CLI_PATH: bundledCliPath() },
      },
      (error, stdout) => resolve(!error && stdout.trim() === "clear"),
    );
  });
}
