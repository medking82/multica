FROM multica-ga401-runtime:20260831-3
USER 0:0
COPY common.py launcher.py updater.py validator.py seed.py claude-release.asc /opt/runtime/hot-update/
COPY launch-codex.sh launch-claude.sh launch-agy.sh /opt/runtime/hot-update/
RUN python3 /opt/runtime/hot-update/seed.py \
    && install -m 0755 /opt/runtime/hot-update/launch-codex.sh /usr/local/bin/codex.new \
    && mv -T /usr/local/bin/codex.new /usr/local/bin/codex \
    && install -m 0755 /opt/runtime/hot-update/launch-claude.sh /usr/local/bin/claude \
    && install -m 0755 /opt/runtime/hot-update/launch-agy.sh /usr/local/bin/agy
USER 1000:1000
LABEL io.hankee.owner="multica-ga401-runtime" io.hankee.cli-hot-update="1"
