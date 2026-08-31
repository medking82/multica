#!/bin/sh
set -eu
umask 077
if [ "$(id -u)" != 1000 ]; then
  echo 'Refusing to run outside the dedicated non-root identity.' >&2
  exit 1
fi
if [ "$#" -gt 0 ]; then
  exec "$@"
fi
printf '%s\n' 'Runtime prepared; waiting for private login and explicit activation.'
while [ ! -f /home/agent/.multica/ga401-activated ]; do
  sleep 5 &
  wait "$!"
done
exec multica --profile ga401 daemon start --foreground \
  --device-name GA401-Isolated --max-concurrent-tasks 2 \
  --workspaces-root /home/agent/workspaces --no-auto-update --no-auto-reload
