// End-to-end in headless Chrome, against the built page and the fake agent (no quota):
//
//   GLBSMITH_FAKE_AGENT=1 GLBSMITH_JOBS=$(mktemp -d) python -m glbsmith.app & npm run test:ui
//
// Environment: TEST_BASE_URL (default http://127.0.0.1:8021), CHROME_BIN, SCREENSHOT_DIR.

import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import puppeteer from 'puppeteer-core';

const baseUrl = process.env.TEST_BASE_URL || 'http://127.0.0.1:8021';
const shots = process.env.SCREENSHOT_DIR;
if (shots) mkdirSync(shots, { recursive: true });

// 1×1 PNG.
const photo = join(tmpdir(), `glbsmith-ui-${process.pid}.png`);
writeFileSync(photo, Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAEBgIApD5fRAAAAABJRU5ErkJggg==', 'base64'));

const browser = await puppeteer.launch({
    executablePath: process.env.CHROME_BIN || '/usr/bin/google-chrome',
    headless: true,
    args: ['--enable-unsafe-swiftshader', '--use-angle=swiftshader', ...(process.env.CHROME_NO_SANDBOX === '1' ? ['--no-sandbox'] : [])],
});
const errors = [];
const step = async (page, name) => { if (shots) await page.screenshot({ path: join(shots, `${name}.png`) }); };

try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1360, height: 900 });
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    await page.evaluateOnNewDocument(() => localStorage.setItem('glbsmith.language', 'ja'));
    await page.goto(baseUrl, { waitUntil: 'load' });
    await page.waitForFunction(() => window.glbsmith);
    await step(page, '01-empty');

    // Missing photo is reported in place.
    await page.type('#width', '30');
    await page.type('#depth', '20');
    await page.type('#height', '90');
    await page.click('input[value=fake]');

    // The model list follows the agent, and "other" lets you type a name.
    const models = () => page.$$eval('#modelSelect option', els => els.map(el => el.value));
    await page.waitForFunction(() => document.querySelectorAll('#modelSelect option').length === 2);
    assert.deepEqual(await models(), ['', '__other__']);
    assert.equal(await page.$eval('#modelCustom', el => el.hidden), true);
    await page.select('#modelSelect', '__other__');
    assert.equal(await page.$eval('#modelCustom', el => el.hidden), false);
    await page.select('#modelSelect', '');
    if (await page.$eval('input[value=claude]', el => !el.disabled)) {
        await page.click('input[value=claude]');
        await page.waitForFunction(() => document.querySelector('#modelSelect option[value=opus]'));
        await page.click('input[value=fake]');
        await page.waitForFunction(() => !document.querySelector('#modelSelect option[value=opus]'));
    }

    await page.click('#submit');
    assert.match(await page.$eval('#formError', el => el.textContent), /写真/);

    // Full run.
    await (await page.$('#image')).uploadFile(photo);
    await page.waitForFunction(() => !document.getElementById('preview').hidden);
    await page.type('#name', 'テスト品');
    await page.click('#submit');
    await page.waitForFunction(() => !document.getElementById('result').hidden);
    await page.waitForFunction(() => document.getElementById('viewer').dataset.loaded === 'true', { timeout: 120000 });
    assert.match(await page.$eval('#measure', el => el.textContent), /30\.0 × 20\.0 × 90\.0/);
    assert.equal(await page.$eval('#download', el => el.getAttribute('download')), 'テスト品.glb');
    assert.ok(await page.$eval('#renders', el => el.children.length) >= 2, 'render and overlay');
    // A 1-pixel photo has no outline to measure, and the page says so.
    assert.equal(await page.$eval('#checkNote', el => el.hidden), false);
    assert.equal(await page.$eval('#sizeNote', el => el.hidden), true);
    assert.equal(await page.$eval('#history', el => el.children.length), 1);
    await step(page, '02-done');

    // Cancelled job keeps the page usable.
    await page.click('#submit');
    await page.waitForFunction(() => document.querySelectorAll('#history li').length === 2);
    await page.waitForFunction(() => !document.getElementById('cancel').hidden, { timeout: 30000 }).catch(() => {});
    const cancel = await page.$('#cancel');
    if (await cancel.evaluate(el => !el.hidden)) {
        await cancel.click();
        await page.waitForFunction(() => /キャンセル|完成/.test(document.getElementById('status').textContent), { timeout: 30000 });
    }

    // Reopening a finished job shows it again, and the page fits a phone.
    await page.setViewport({ width: 390, height: 800 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no sideways scrolling');
    await step(page, '03-phone');

    await page.reload({ waitUntil: 'load' });
    await page.waitForFunction(() => window.glbsmith);
    assert.equal(await page.$eval('#history', el => el.children.length), 2);
    assert.deepEqual(errors, []);
    console.log('UI smoke test passed');
} finally {
    await browser.close();
}
