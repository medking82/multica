#!/bin/sh
set -eu
test "$(id -u)" = 1000
test ! -e /var/run/docker.sock
test ! -e /run/docker.sock
test ! -e /home/marck
test ! -e /home/agent/.multica/ga401-activated
python3 /opt/runtime/verify-codex-bundle.py /opt/codex/0.156.1 --entrypoint /usr/local/bin/codex --probe
test "$(codex --version)" = 'codex-cli 0.156.1'
claude --version | grep -F '2.1.280'
agy --version | grep -F '1.2.8'
test "$(sha256sum /usr/local/bin/multica | awk '{print $1}')" = \
  '96d01b201afda32dd2a509518f74ecfcf4303121ee66e422e23f1387d9be89d4'
multica --version | grep -F '0.4.39-ga401.84af6325'
node --version
pnpm --version
python3 --version
gh --version | head -n 1
playwright --version
playwright-mcp --help >/dev/null
python3 -c 'import errno, os; exec("try:\n os.chroot(\"/tmp\")\nexcept PermissionError as e:\n assert e.errno == errno.EPERM\nelse:\n raise AssertionError(\"outer container unexpectedly has chroot privilege\")")'
unshare --user --map-root-user python3 -c 'import os; os.chroot("/tmp"); print("namespaced chroot PASS; outer identity remains unprivileged")'
node /opt/runtime/browser-smoke.cjs
node /opt/runtime/mcp-smoke.cjs
printf '%s\n' 'Required executable and browser smoke PASS; optional compatibility results reported above. No login, inference, or daemon start performed.'
