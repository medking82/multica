# Native dictation (Windows Desktop)

The mic in Chat, ticket comments/replies, and both ticket-creation composers delegates to the user's running Codex Desktop global dictation UI. Multica does not record audio or call a transcription API. Web, mobile, macOS and Linux do not receive this adapter or a mic entry.

## Setup and behavior

1. Run the official packaged Codex Desktop in the same Windows interactive session. The helper validates its Windows package identity, not just its executable name. An unpackaged build is deliberately not supported.
2. In Codex, configure the global dictation toggle as `Ctrl+Alt+Shift+D`, with no conflicting binding. This bridge expects one unconditional `globalDictationToggle` entry in Codex's `keybindings.json`; it never edits that file. `CODEX_HOME`, when set, must be absolute.
3. Focus a Multica composer, release keyboard modifiers, and click the mic. The editor preserves its selection while taking focus. Lazy comment/reply editors are mounted before dispatch.
4. Finish dictation in the native Codex UI. Text insertion and recording are owned by Codex. Multica does not submit the resulting text automatically.

A success notice means only that the shortcut was dispatched, not that audio recording or transcription succeeded. Account eligibility, subscription usage, microphone permission, availability and future compatibility remain controlled by Codex; this feature promises no free or unlimited usage. A missing app, unavailable account feature or failed bridge does not enable an API fallback.

## Boundaries

- The renderer exposes a parameterless adapter. The main process admits only a registered, focused, trusted top-level renderer with an active user gesture and an editable input.
- Multica reads only the bounded keybindings file. It does not read Codex auth files, sessions, cookies, credentials or a keychain, and does not send anything to a Multica server for dictation.
- The bundled Multica CLI implements the private `--desktop-dictation-v1` helper before ordinary CLI profile/update handling. It accepts only a read-only `probe`, or `toggle` with a canonical nonzero decimal native window handle.
- The Windows helper verifies the current foreground window, official Codex package identity and interactive session, and rejects held modifiers/submit keys. It sends only the fixed chord, with bounded partial-injection cleanup and no toggle retry. It does not elevate, change permissions, start/stop agents or execute user scripts.
- Only one toggle can be pending across all Multica windows. Unknown state, a missing/old bundled helper or a timeout fails closed.

## Verification

Automated tests use mocked Electron/IPC/editor adapters and fake native platforms. Windows ABI tests encode INPUT structures without sending keyboard input. They cover focus, frame/origin, gesture, configuration, native identity, held-key, partial-input, lazy editor and no-network/no-audio behavior.

Real recording and transcript insertion require a manual acceptance check with an eligible signed-in Codex account and microphone permission. Do not treat the read-only helper probe or the automated tests as end-to-end audio verification.
