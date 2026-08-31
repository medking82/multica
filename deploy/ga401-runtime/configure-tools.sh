#!/bin/sh
set -eu
umask 077
test "$(id -u)" = 1000
test "$HOME" = /home/agent
multica --profile ga401 config set server_url https://agent.hankee.com
multica --profile ga401 config set app_url https://agent.hankee.com
multica --profile ga401 config set workspace_id bca51e7c-832c-4c6e-89a9-54ce8ea3bda1
codex mcp add runtime-browser -- /opt/runtime/browser-mcp.sh
claude mcp add --scope user runtime-browser /opt/runtime/browser-mcp.sh
agy mcp add runtime-browser /opt/runtime/browser-mcp.sh
printf '%s\n' 'Private runtime profile and native stdio browser MCP configured; no account login performed.'
