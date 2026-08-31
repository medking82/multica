'use strict';
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true, chromiumSandbox: true });
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.setContent('<title>GA401 browser smoke</title><button>Verify</button>');
    await page.getByRole('button', { name: 'Verify' }).click();
    assert.equal(await page.title(), 'GA401 browser smoke');
    const png = await page.screenshot();
    assert.ok(png.length > 100);
    console.log(`sandboxed Chromium ${browser.version()}: DOM, click and screenshot PASS`);
    await context.close();
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
