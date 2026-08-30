# Windows x64 update installation guard

Windows can keep the bundled Multica CLI executable locked while it is running. Desktop must not terminate that runtime to make an update succeed: it may own active agent runs after the GUI closes.

For Windows x64 Desktop, this guard replaces electron-updater's synchronous install-on-quit decision with a bounded, read-only process check:

- A process named `multica.exe` at the current bundled executable path blocks installation. A matching process whose executable path cannot be read also blocks it conservatively.
- A failed, timed-out or unknown probe blocks installation. It never kills a process, reads account credentials or executes renderer-supplied scripts.
- On an ordinary quit, Desktop closes without installing when the runtime is busy/unknown. The downloaded update remains pending. A quit received during an explicit install check is remembered, not lost.
- On **Restart now**, a busy/unknown result keeps Desktop open and offers **Retry installation**. Finish active runs first, then open **Runtimes**, select this computer, and choose **Stop** before retrying. The guard never stops the runtime itself.
- Concurrent install requests share one check. The notification disables its action while checking, explains deferral, and hydrates its current state when remounted. A slow snapshot cannot replace a newer event. A newer download preserves checking state and its version.
- A clear probe only authorizes dispatch at the final successful `quit` event. Another `before-quit`/`will-quit` listener can still cancel closing without starting the installer. A later quit (including a daemon listener's asynchronous continuation) gets a fresh probe, not a permanent bypass.
- Automatic checks/downloads, saved update preferences, official release/changelog URLs and architecture-specific feeds do not change. macOS, Linux and Windows arm64 keep their existing installation path in this scoped change.

The existing runtime preferences remain unchanged: `autoStart: true`, `autoStop: false`. A deliberately persistent bundled runtime will continue blocking installation after the GUI closes. This is expected; finish runs and stop it explicitly when ready. Enabling auto-stop is not required or performed by this change. Existing daemon stop/quit policy is not replaced by the updater guard.

The check uses a fixed system PowerShell command with a six-second timeout and bounded output. It observes process state before handing off to the existing installer; it is not a cross-process lock that prevents a different process from starting the CLI later. The OS and installer retain responsibility for races after that handoff. Installer invocation at the final `quit` phase follows electron-updater's own install-on-quit behavior; its public `quitAndInstall` dispatch is synchronous.

## State and diagnostics

Main owns `idle`, `ready`, `checking`, and `deferred` install state. Events and `updater:get-install-state` expose the same typed value through preload. This is in-memory state, not a second download store: electron-updater retains its existing cached download behavior, and a subsequent check on a new app launch repopulates the notification. With automatic checks disabled, use the existing manual update check.

`runtime_running` means a matching process (or unreadable matching executable path) blocked installation. `probe_failed` carries one bounded diagnostic: `system_root_missing`, `launch_failed`, `timed_out`, `invalid_output`, or `probe_failed`. Logs and notification never include process paths, arguments, environment values or raw PowerShell stderr. Restore system PowerShell/CIM availability before retrying a failed probe. No installer is invoked on these results.

## Verification

Unit tests cover the fixed probe invocation, bounded diagnostics, quit interleavings, final-quit dispatch, platform/architecture defaults, and notification rehydration/retry. They use fakes and never launch an installer or stop a daemon.

Two opt-in Windows smoke scripts exercise native behavior from the actual source. Run from `apps/desktop` after installing the repository's dependencies:

```powershell
node scripts/update-install-guard-smoke.mjs
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
foreach ($scenario in @('safe-restart', 'safe-quit', 'quit-during-check', 'cancel-then-quit', 'daemon-continuation', 'will-cancel')) {
  & .\node_modules\electron\dist\electron.exe scripts/update-lifecycle-smoke.mjs "--case=$scenario"
  if ($LASTEXITCODE -ne 0) { throw "Lifecycle smoke failed: $scenario" }
}
```

- The CIM smoke creates its own disposable `multica.exe` process from Node, including spaces, an apostrophe and Unicode in its path. It verifies running exact-path, nonmatching-path and stopped-fixture cases. Only this fixture is started/stopped; no installed Multica agent is used.
- The Electron smoke uses a hidden fixture window and isolated temporary profile. Real quit events exercise cancellation and a one-shot asynchronous listener continuation. The process probe and installer are fake; dispatch must occur only after final quit, and no real installer is run. It does not claim to stop/test a real daemon.
- Both scripts passed on Windows x64; lifecycle cases used Electron 39.8.7. They are explicit smoke checks, not part of the default cross-platform unit command.

Real locked-executable NSIS installer acceptance still requires an isolated Windows test installation and is not performed by these checks. Do not run it against an active production runtime. Passing the smoke does not prove installation is atomic, nor does it change any user's app, profile or update feed.
