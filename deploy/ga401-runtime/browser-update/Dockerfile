FROM multica-ga401-runtime:20260831-hot2 AS runtime
USER 0:0
COPY browser_common.py browser_updater.py browser_validator.py browser_launcher.py \
     browser_watch.py browser_probe.cjs profile_guard.py seed_browser.py /opt/runtime/browser-update/
COPY launch-playwright.sh launch-mcp.sh /opt/runtime/browser-update/
RUN python3 /opt/runtime/browser-update/seed_browser.py \
    && install -m 0755 /opt/runtime/browser-update/launch-playwright.sh /usr/local/bin/playwright.new \
    && mv -T /usr/local/bin/playwright.new /usr/local/bin/playwright \
    && install -m 0755 /opt/runtime/browser-update/launch-mcp.sh /usr/local/bin/playwright-mcp.new \
    && mv -T /usr/local/bin/playwright-mcp.new /usr/local/bin/playwright-mcp \
    && install -m 0755 /opt/runtime/browser-update/launch-mcp.sh /opt/runtime/browser-mcp.sh
ENV NODE_PATH=/opt/browser-tools/current/node_modules:/usr/local/lib/node_modules \
    PLAYWRIGHT_BROWSERS_PATH=0 PLAYWRIGHT_SKIP_BROWSER_GC=1
USER 1000:1000
LABEL io.hankee.browser-update="1"

FROM multica-ga401-browser:20260831-1 AS login
USER 0:0
COPY --from=runtime /opt/runtime/browser-update/ /opt/runtime/browser-update/
COPY --from=runtime /opt/runtime/hot-update/common.py /opt/runtime/hot-update/common.py
RUN install -m 0755 /opt/runtime/browser-update/launch-playwright.sh /usr/local/bin/playwright.new \
    && mv -T /usr/local/bin/playwright.new /usr/local/bin/playwright \
    && install -m 0755 /opt/runtime/browser-update/launch-mcp.sh /usr/local/bin/playwright-mcp.new \
    && mv -T /usr/local/bin/playwright-mcp.new /usr/local/bin/playwright-mcp \
    && install -m 0755 /opt/runtime/browser-update/launch-mcp.sh /opt/runtime/browser-mcp.sh \
    && install -d -m 0755 /opt/browser-tools /opt/validation-results
ENV NODE_PATH=/opt/browser-tools/current/node_modules:/usr/local/lib/node_modules \
    PLAYWRIGHT_BROWSERS_PATH=0 PLAYWRIGHT_SKIP_BROWSER_GC=1
USER 1000:1000
ENTRYPOINT ["python3", "/opt/runtime/browser-update/browser_launcher.py", "login"]
LABEL io.hankee.browser-update="1"
