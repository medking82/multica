FROM node:22-trixie-slim@sha256:7d7efef430da5e6837687b77d3c2ff87473d0b3a365fde36a4ab65f77f1671c7

ENV DEBIAN_FRONTEND=noninteractive \
    HOME=/home/agent \
    NODE_PATH=/usr/local/lib/node_modules \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers \
    DISABLE_AUTOUPDATER=1 \
    MULTICA_DAEMON_AUTO_UPDATE=false \
    MULTICA_DAEMON_AUTO_RELOAD=false \
    MULTICA_CODEX_PATH=/usr/local/bin/codex \
    MULTICA_CLAUDE_PATH=/usr/local/bin/claude \
    MULTICA_ANTIGRAVITY_PATH=/usr/local/bin/agy \
    MULTICA_WORKSPACES_ROOT=/home/agent/workspaces \
    PYTHONUTF8=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      bash build-essential ca-certificates curl gh git gpgv jq openssh-client \
      pkg-config procps python3 python3-venv ripgrep unzip \
    && npm install --global pnpm@10.28.2 playwright@1.62.1 @playwright/mcp@0.0.79 \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && usermod --login agent --home /home/agent node \
    && groupmod --new-name agent node \
    && install -d -o 1000 -g 1000 -m 0700 /home/agent \
       /home/agent/workspaces /home/agent/.multica /home/agent/.codex \
       /home/agent/.gemini /home/agent/.gemini/antigravity-cli

COPY --chmod=0755 .assets/claude .assets/agy .assets/multica /usr/local/bin/
COPY .assets/codex-bundle/ /opt/codex/0.156.1/
COPY verify-codex-bundle.py codex-bundle.sha256 /opt/runtime/
RUN ln -s /opt/codex/0.156.1/bin/codex /usr/local/bin/codex \
    && python3 /opt/runtime/verify-codex-bundle.py /opt/codex/0.156.1 --entrypoint /usr/local/bin/codex
COPY .assets/LICENSE .assets/NOTICE /usr/local/share/licenses/multica/
COPY --chmod=0755 entrypoint.sh smoke.sh browser-mcp.sh configure-tools.sh /opt/runtime/
COPY browser-smoke.cjs mcp-smoke.cjs browser-config.json /opt/runtime/
COPY --chown=1000:1000 --chmod=0600 antigravity-settings.json /home/agent/.gemini/antigravity-cli/settings.json
COPY --chown=1000:1000 --chmod=0600 codex-config.toml /home/agent/.codex/config.toml

USER 1000:1000
WORKDIR /home/agent
RUN getconf GNU_LIBC_VERSION | grep -E '^glibc 2\.(3[89]|4[0-9])$'
RUN /opt/runtime/configure-tools.sh
ENTRYPOINT ["/opt/runtime/entrypoint.sh"]
