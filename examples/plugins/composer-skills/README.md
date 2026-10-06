# Workspace Skill Picker

This installable example adds `/skills` to the composer. It opens a modal,
lists the current workspace's Skill names and descriptions, and inserts the
selected Skill marker into the draft. The user reviews and submits the draft
through the normal composer.

The plugin requests only `skills:read`. The host returns Skill IDs and display
summaries for the installation's workspace; Skill bodies and configuration
are not included. The iframe receives neither draft text nor credentials, and
it has no network scope. Selection asks the host composer to insert a marker;
it cannot submit the draft.

## Install

Zip this folder, including `multica.plugin.json` and `ui/main.js`, then upload
it in **Settings → Plugins**. No plugin server or credentials are needed.

The command is available in chat, issue comments and replies, issue creation,
and agent creation. In a composer, type `/skills`, choose a result, then review
the inserted marker before sending.
