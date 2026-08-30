# Windows x64 update installation guard

Windows can keep the bundled Multica CLI executable locked while it is running. Desktop must not terminate that runtime to make an update succeed: it may own active agent runs after the GUI closes.

For Windows x64 Desktop, this guard replaces electron-updater's synchronous install-on-quit decision with a bounded, read-only process check:

- A process named `multica.exe` at the current bundled executable path blocks installation. A matching process whose executable path cannot be read also blocks it conservatively.
- A failed, timed-out or unknown probe blocks installation. It never kills a process, reads account credentials or executes renderer-supplied scripts.
- On an ordinary quit, Desktop closes without installing when the runtime is busy/unknown. The downloaded update remains pending.
- On **Restart now**, a busy/unknown result keeps Desktop open and explains how to finish active runs and stop the Desktop runtime before trying again.
- Automatic checks/downloads, saved update preferences, official release/changelog URLs and architecture-specific feeds do not change. macOS, Linux and Windows arm64 keep their existing installation path in this scoped change.

The check uses a fixed system PowerShell command with a six-second timeout and bounded output. It observes process state immediately before handing off to the existing installer; it is not a cross-process lock that prevents a different process from starting the CLI later. The OS and installer retain responsibility for races after that handoff.

Tests use mocked process probes, Electron lifecycle events and updater calls. Real locked-executable installer acceptance requires a Windows test installation and is not performed by the unit suite. Do not run it against an active production runtime.
