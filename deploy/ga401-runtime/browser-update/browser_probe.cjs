'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { spawn } = require('node:child_process');

async function mcpCheck(generation, fixture) {
  const child = spawn('python3', ['/opt/runtime/browser-update/browser_launcher.py',
    '--generation', generation, 'mcp'], { stdio: ['pipe', 'pipe', 'pipe'] });
  let sequence = 0, buffer = '', diagnostic = '';
  const pending = new Map();
  child.stderr.on('data', data => { diagnostic = (diagnostic + data).slice(-3000); });
  child.stdout.on('data', data => {
    buffer += data;
    assert.ok(buffer.length < 1024 * 1024, 'MCP output is bounded');
    let newline;
    while ((newline = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, newline);
      buffer = buffer.slice(newline + 1);
      const message = JSON.parse(line);
      const waiter = pending.get(message.id);
      if (!waiter) continue;
      pending.delete(message.id);
      clearTimeout(waiter.timer);
      if (message.error) waiter.reject(new Error(JSON.stringify(message.error)));
      else waiter.resolve(message.result);
    }
  });
  child.on('exit', code => {
    for (const waiter of pending.values()) {
      clearTimeout(waiter.timer);
      waiter.reject(new Error(`MCP exited ${code}: ${diagnostic}`));
    }
    pending.clear();
  });
  function request(method, params) {
    const id = ++sequence;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`MCP ${method} timed out: ${diagnostic}`));
      }, 20000);
      pending.set(id, { resolve, reject, timer });
      child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
    });
  }
  try {
    await request('initialize', { protocolVersion: '2025-03-26', capabilities: {},
      clientInfo: { name: 'ga401-browser-update-check', version: '1' } });
    child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n');
    const { tools } = await request('tools/list', {});
    assert.ok(tools.some(tool => tool.name === 'browser_navigate'));
    const result = await request('tools/call', { name: 'browser_navigate', arguments: { url: fixture } });
    assert.notEqual(result.isError, true, JSON.stringify(result.content));
    assert.match(JSON.stringify(result.content), /Browser update fixture/);
    await request('tools/call', { name: 'browser_close', arguments: {} });
  } finally {
    for (const waiter of pending.values()) clearTimeout(waiter.timer);
    child.stdin.end();
    child.kill('SIGTERM');
    if (child.exitCode === null && child.signalCode === null) {
      await new Promise(resolve => child.once('exit', resolve));
    }
  }
}

(async () => {
  const directory = fs.realpathSync(process.argv[2]);
  const record = JSON.parse(fs.readFileSync(path.join(directory, 'release.json'), 'utf8'));
  assert.ok(fs.realpathSync(require.resolve('playwright')).startsWith(directory + path.sep));
  const { chromium } = require('playwright');
  assert.equal(chromium.executablePath(), path.join(directory, record.executable));
  const checks = [];
  // Exercise both native headless-shell and the full Chromium executable used by MCP/login.
  const browser = await chromium.launch({ headless: true, chromiumSandbox: true });
  try {
    assert.equal(browser.version(), record.chromium);
    const page = await browser.newPage();
    await page.setContent('<title>Browser update fixture</title><button>Verify</button>');
    await page.getByRole('button', { name: 'Verify' }).click();
    assert.ok((await page.screenshot()).length > 100);
    checks.push('sandboxed-headless-dom-click-screenshot');
  } finally { await browser.close(); }
  const server = http.createServer((_, response) => {
    response.setHeader('Content-Type', 'text/html');
    response.end('<title>Browser update fixture</title><button>Verify</button>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const fixture = `http://127.0.0.1:${server.address().port}/`;
  try {
    const profile = path.join(process.env.HOME, 'synthetic-profile');
    for (let run = 0; run < 2; run++) {
      const context = await chromium.launchPersistentContext(profile, {
        executablePath: chromium.executablePath(), chromiumSandbox: true,
        headless: !process.argv.includes('--headed'),
      });
      try {
        const page = await context.newPage();
        await page.goto(fixture);
        if (run === 0) {
          await page.evaluate(() => { localStorage.setItem('synthetic', 'retained'); });
          await context.addCookies([{ name: 'synthetic', value: 'retained', url: fixture,
            expires: Math.floor(Date.now() / 1000) + 3600 }]);
        } else {
          assert.equal(await page.evaluate(() => localStorage.getItem('synthetic')), 'retained');
          assert.ok((await context.cookies()).some(cookie => cookie.name === 'synthetic' && cookie.value === 'retained'));
        }
      } finally { await context.close(); }
    }
    checks.push(process.argv.includes('--headed') ? 'sandboxed-headed-profile-restart' : 'sandboxed-full-chromium-profile-restart');
    await mcpCheck(record.generation, fixture);
    checks.push('stdio-mcp-initialize-tools-local-navigation-close');
  } finally { await new Promise(resolve => server.close(resolve)); }
  console.log(JSON.stringify({ passed: true, generation: record.generation, checks }));
})().catch(error => { console.error(error); process.exitCode = 1; });
