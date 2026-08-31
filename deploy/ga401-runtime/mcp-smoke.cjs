'use strict';
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const http = require('node:http');

(async () => {
  const server = http.createServer((_, response) => {
    response.setHeader('Content-Type', 'text/html');
    response.end('<title>GA401 MCP smoke</title><button>Verify</button>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const child = spawn('/opt/runtime/browser-mcp.sh', [], { stdio: ['pipe', 'pipe', 'pipe'] });
  let sequence = 0;
  let buffer = '';
  let diagnostic = '';
  const pending = new Map();
  child.stderr.on('data', data => { diagnostic = (diagnostic + data).slice(-4000); });
  child.stdout.on('data', data => {
    buffer += data;
    let newline;
    while ((newline = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, newline);
      buffer = buffer.slice(newline + 1);
      try {
        const message = JSON.parse(line);
        const waiter = pending.get(message.id);
        if (!waiter) continue;
        pending.delete(message.id);
        clearTimeout(waiter.timer);
        if (message.error) waiter.reject(new Error(JSON.stringify(message.error)));
        else waiter.resolve(message.result);
      } catch (error) {
        for (const waiter of pending.values()) waiter.reject(error);
      }
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
      clientInfo: { name: 'ga401-runtime-smoke', version: '1' } });
    child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n');
    const { tools } = await request('tools/list', {});
    assert.ok(tools.some(tool => tool.name === 'browser_navigate'));
    const result = await request('tools/call', { name: 'browser_navigate',
      arguments: { url: `http://127.0.0.1:${server.address().port}/` } });
    assert.notEqual(result.isError, true, JSON.stringify(result.content));
    assert.match(JSON.stringify(result.content), /GA401 MCP smoke/);
    await request('tools/call', { name: 'browser_close', arguments: {} });
    console.log('stdio Playwright MCP: initialize, tool discovery, local page navigation PASS');
  } finally {
    for (const waiter of pending.values()) clearTimeout(waiter.timer);
    child.stdin.end();
    child.kill('SIGTERM');
    server.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
