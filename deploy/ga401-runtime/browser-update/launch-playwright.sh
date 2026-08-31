#!/bin/sh
set -eu
exec python3 /opt/runtime/browser-update/browser_launcher.py playwright "$@"
