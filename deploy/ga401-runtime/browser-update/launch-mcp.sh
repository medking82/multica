#!/bin/sh
set -eu
umask 077
exec python3 /opt/runtime/browser-update/browser_launcher.py mcp "$@"
