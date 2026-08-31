#!/bin/sh
set -eu
umask 077
browser_path=$(node -e 'process.stdout.write(require("playwright").chromium.executablePath())')
exec playwright-mcp --headless --isolated \
  --config /opt/runtime/browser-config.json --executable-path "$browser_path" "$@"
