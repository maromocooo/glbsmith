// End-to-end in headless Chrome, against the built page and the fake agent (no quota):
//
//   GLBSMITH_FAKE_AGENT=1 GLBSMITH_JOBS=$(mktemp -d) python -m glbsmith.app & npm run test:ui
//
// Environment: TEST_BASE_URL (default http://127.0.0.1:8021), CHROME_BIN, SCREENSHOT_DIR.

import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { crc32, deflateSync } from 'node:zlib';
import puppeteer from 'puppeteer-core';

const baseUrl = process.env.TEST_BASE_URL || 'http://127.0.0.1:8021';
const shots = process.env.SCREENSHOT_DIR;
if (shots) mkdirSync(shots, { recursive: true });

function png(width, height) {
    const chunk = (type, data) => {
        const body = Buffer.concat([Buffer.from(type), data]);
        const out = Buffer.alloc(body.length + 8);
        out.writeUInt32BE(data.length, 0);
        body.copy(out, 4);
        out.writeInt32BE(crc32(body) | 0, body.length + 4);
        return out;
    };
    const header = Buffer.alloc(13);
    header.writeUInt32BE(width, 0);
    header.writeUInt32BE(height, 4);
    header.set([8, 2, 0, 0, 0], 8);
    const rows = Buffer.alloc((width * 3 + 1) * height, 200);
    for (let y = 0; y < height; y++) rows[y * (width * 3 + 1)] = 0;
    return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header), chunk('IDAT', deflateSync(rows)), chunk('IEND', Buffer.alloc(0))]);
}

// 1×1 PNG.
const photo = join(tmpdir(), `glbsmith-ui-${process.pid}.png`);
writeFileSync(photo, Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAEBgIApD5fRAAAAABJRU5ErkJggg==', 'base64'));

// A larger image to paint on: a 200×150 white PNG made in the browser would need a page, so build it here.
const bigPhoto = join(tmpdir(), `glbsmith-ui-big-${process.pid}.png`);
writeFileSync(bigPhoto, png(200, 150));

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
    await page.waitForFunction(() => document.querySelectorAll('#photos li').length === 1);

    // A second photo, painted in the modal: brush a stroke, then save.
    await (await page.$('#image')).uploadFile(bigPhoto);
    await page.waitForFunction(() => document.querySelectorAll('#photos li').length === 2);
    const paintButton = (await page.$$('#photos li:nth-child(2) button.small'))[0];
    await paintButton.click();
    await page.waitForFunction(() => document.getElementById('paint').open);
    const box = await (await page.$('#paintLayer')).boundingBox();
    await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.3);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.7, box.y + box.height * 0.3, { steps: 6 });
    await page.mouse.up();
    await step(page, '015-painting');
    const inked = () => page.$eval('#paintLayer', c => c.getContext('2d').getImageData(0, 0, c.width, c.height).data.filter((v, i) => i % 4 === 3 && v > 127).length);
    const afterStroke = await inked();
    assert.ok(afterStroke > 0);
    // Undo removes it; redo the stroke; bucket fill paints the rest of the empty area.
    await page.click('#paintUndo');
    assert.equal(await inked(), 0);
    await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.3);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.7, box.y + box.height * 0.3, { steps: 6 });
    await page.mouse.up();
    await page.click('[data-paint-tool=fill]');
    await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.8);
    assert.ok(await inked() > afterStroke);
    await page.click('#paintUndo');
    assert.equal(await inked(), afterStroke);
    await page.click('#paintSave');
    await page.waitForFunction(() => !document.getElementById('paint').open && document.querySelector('#photos canvas[data-painted]'));
    // Esc in the modal keeps what was saved.
    await (await page.$$('#photos li:nth-child(2) button.small'))[0].click();
    await page.waitForFunction(() => document.getElementById('paint').open);
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.getElementById('paint').open);
    assert.ok(await page.$('#photos canvas[data-painted]'));
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
    assert.equal(await page.$$eval('#referenceStrip button', els => els.length), 2);
    assert.equal(await page.$$eval('#photos li', els => els.length), 0, 'form is ready for the next item');
    await step(page, '02-done');
    const saved = await page.evaluate(async () => (await (await fetch('/api/jobs')).json())[0].photos);
    assert.deepEqual(saved, [{ painted: false }, { painted: true }]);

    // How many at once: changing it reaches the server.
    await page.waitForSelector('#concurrency option');
    await page.select('#concurrency', '2');
    await page.waitForFunction(async () => (await (await fetch('/api/settings')).json()).concurrency === 2);
    assert.match(await page.$eval('#load', el => el.textContent), /実行中 \d+/);
    await page.select('#concurrency', '1');

    // Cancelled job keeps the page usable.
    await (await page.$('#image')).uploadFile(photo);
    await page.waitForFunction(() => document.querySelectorAll('#photos li').length === 1);
    for (const [id, value] of [['width', '30'], ['depth', '20'], ['height', '90']]) await page.type(`#${id}`, value);
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
