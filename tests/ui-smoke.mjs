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

    // Options: an API key adds an "(API)" agent, and the page never gets the key back.
    const hasApiAgent = () => page.$('input[value=claude-api]').then(Boolean);
    assert.equal(await hasApiAgent(), false);
    await page.click('#optionsOpen');
    await page.waitForFunction(() => document.getElementById('options').open);
    await page.type('.key-row[data-provider=anthropic] input', 'sk-ui-test-0123456789abcd');
    await page.click('.key-row[data-provider=anthropic] button.primary');
    await page.waitForFunction(() => document.querySelector('.key-row[data-provider=anthropic] small').textContent.includes('…abcd'));
    await step(page, '005-options');
    assert.equal(await page.$eval('.key-row[data-provider=anthropic] input', el => el.value), '');
    assert.ok(!(await page.content()).includes('sk-ui-test'), 'the key is not in the page');
    if (await page.evaluate(async () => (await (await fetch('/api/status')).json()).agents.some(a => a.id === 'claude'))) {
        await page.waitForSelector('input[value=claude-api]');
        await page.click('#optionsClose');
        await page.click('input[value=claude-api]');
        assert.equal(await page.$eval('#billingNote', el => el.hidden), false, 'says it is billed per use');
        await page.click('input[value=claude]');
        assert.equal(await page.$eval('#billingNote', el => el.hidden), true);
        await page.click('#optionsOpen');
        await page.waitForFunction(() => document.getElementById('options').open);
    }
    await page.click('.key-row[data-provider=anthropic] .inputs button:last-child');
    await page.waitForFunction(() => document.querySelector('.key-row[data-provider=anthropic] small').textContent === '未設定');
    await page.click('#optionsClose');
    assert.equal(await hasApiAgent(), false);

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
    // Tokens used: short in the history, in full on the result.
    assert.match(await page.$eval('#history small', el => el.textContent), /完成 · fake · 1\.2万 トークン/);
    assert.match(await page.$eval('#status', el => el.textContent), /合計 12,345 トークン（入力 12,000、うちキャッシュ 9,000 ／ 出力 345）/);
    assert.equal(await page.$$eval('#referenceStrip button', els => els.length), 2);
    assert.equal(await page.$$eval('#photos li', els => els.length), 0, 'form is ready for the next item');
    await step(page, '02-done');
    const saved = await page.evaluate(async () => (await (await fetch('/api/jobs')).json())[0].photos);
    assert.deepEqual(saved, [{ painted: false }, { painted: true }]);

    // Changes: paint on the model in 3D, per colour, then start a revision.
    await page.click('#reviseStart');
    await page.waitForFunction(() => !document.getElementById('revise').hidden);
    const viewerBox = await (await page.$('#viewer canvas')).boundingBox();
    const middle = { x: viewerBox.x + viewerBox.width / 2, y: viewerBox.y + viewerBox.height / 2 };
    const spots = id => page.evaluate(i => window.glbsmith.viewer.marks(i).length, id);
    const stroke = async () => {
        await page.mouse.move(middle.x, middle.y - 30);
        await page.mouse.down();
        for (let i = 0; i < 6; i++) {
            await page.mouse.move(middle.x, middle.y - 30 + i * 12);
            await new Promise(r => setTimeout(r, 60));
        }
        await page.mouse.up();
    };
    await page.click('#reviseSubmit');
    assert.match(await page.$eval('#reviseError', el => el.textContent), /色を塗って/);
    await stroke();
    const painted = await spots(0);
    assert.ok(painted > 6, 'a drag paints a connected line, not one spot per mouse event');
    await step(page, '025-revising');
    // Painted without an instruction is refused, in place.
    await page.click('#reviseSubmit');
    assert.match(await page.$eval('#reviseError', el => el.textContent), /色 1 は塗ってありますが/);
    // Undo, and a second colour, and painting moves the camera for nobody.
    const cameraBefore = await page.evaluate(() => window.glbsmith.viewer.camera.position.toArray());
    await stroke();
    assert.deepEqual(await page.evaluate(() => window.glbsmith.viewer.camera.position.toArray()), cameraBefore);
    await page.click('#reviseUndo');
    assert.equal(await spots(0), painted);
    await page.click('#addColour');
    assert.equal(await page.$$eval('#colours li', els => els.length), 2);
    await page.mouse.move(middle.x + 4, middle.y + 80);
    await page.mouse.down();
    await new Promise(r => setTimeout(r, 80));
    await page.mouse.up();
    assert.ok(await spots(1) > 0, 'second colour');
    // An image attached to the first colour's instruction, one to the whole-model one, and one removed again.
    const pick = async host => (await page.$(`${host} input[type=file]`)).uploadFile(bigPhoto);
    await pick('#colours li:nth-child(1) .attachments');
    await page.waitForFunction(() => document.querySelectorAll('#colours li:nth-child(1) .attached').length === 1);
    await pick('#colours li:nth-child(1) .attachments');
    await page.waitForFunction(() => document.querySelectorAll('#colours li:nth-child(1) .attached').length === 2);
    await page.click('#colours li:nth-child(1) .attached button');
    await page.waitForFunction(() => document.querySelectorAll('#colours li:nth-child(1) .attached').length === 1);
    await pick('#generalAttachments');
    await page.waitForFunction(() => document.querySelectorAll('#generalAttachments .attached').length === 1);
    // Pasting while making changes goes to the instruction, not the new-item form.
    const formPhotos = await page.$$eval('#photos li', els => els.length);
    await page.evaluate(async () => {
        const data = new DataTransfer();
        const blob = await (await fetch(document.querySelector('#generalAttachments img').src)).blob();
        data.items.add(new File([blob], 'pasted.png', { type: 'image/png' }));
        document.getElementById('reviseGeneral').dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true }));
    });
    await page.waitForFunction(() => document.querySelectorAll('#generalAttachments .attached').length === 2);
    assert.equal(await page.$$eval('#photos li', els => els.length), formPhotos);
    await page.click('#generalAttachments .attached button');
    await step(page, '026-attached');

    // Pasting images into an instruction: any number, and typing carries on where it was.
    const paste = (selector, count) => page.evaluate(async (target, n) => {
        const box = document.querySelector(target);
        box.focus();
        const blob = await (await fetch(document.querySelector('#generalAttachments img').src)).blob();
        for (let i = 0; i < n; i++) {
            const data = new DataTransfer();
            data.items.add(new File([blob], `pasted-${i}.png`, { type: 'image/png' }));
            document.activeElement.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true }));
        }
    }, selector, count);
    await paste('#colours li:nth-child(2) textarea', 6);
    await page.waitForFunction(() => document.querySelectorAll('#colours li:nth-child(2) .attached').length === 6);
    assert.equal(await page.evaluate(() => document.activeElement === document.querySelector('#colours li:nth-child(2) textarea')), true, 'still typing');
    assert.equal(await page.$$eval('#colours li:nth-child(1) .attached', els => els.length), 1, 'the other instruction is untouched');
    // Dropping on the text box counts too.
    await page.evaluate(async () => {
        const box = document.querySelector('#colours li:nth-child(2) textarea');
        const blob = await (await fetch(document.querySelector('#generalAttachments img').src)).blob();
        const data = new DataTransfer();
        data.items.add(new File([blob], 'dropped.png', { type: 'image/png' }));
        box.dispatchEvent(new DragEvent('drop', { dataTransfer: data, bubbles: true, cancelable: true }));
    });
    await page.waitForFunction(() => document.querySelectorAll('#colours li:nth-child(2) .attached').length === 7);
    const prompts = await page.$$('#colours textarea');
    await prompts[0].type('上を長くして');
    await prompts[1].type('ここはそのまま');
    // The model can be chosen too: the list follows the agent, and "other" takes a typed name.
    await page.waitForFunction(() => document.querySelector('#reviseModelSelect option[value=__other__]'));
    if (await page.$eval('#reviseAgent', el => [...el.options].some(o => o.value === 'claude'))) {
        await page.select('#reviseAgent', 'claude');
        await page.waitForFunction(() => document.querySelector('#reviseModelSelect option[value=opus]'));
        await page.select('#reviseAgent', 'fake');
        await page.waitForFunction(() => !document.querySelector('#reviseModelSelect option[value=opus]'));
    }
    await page.select('#reviseModelSelect', '__other__');
    assert.equal(await page.$eval('#reviseModelCustom', el => el.hidden), false);
    await page.type('#reviseModel', 'my-model-1');
    await page.click('#reviseSubmit');
    await page.waitForFunction(() => document.getElementById('resultTitle').textContent.includes('修正 1'));
    assert.equal(await page.evaluate(async () => (await (await fetch('/api/jobs')).json())[0].model), 'my-model-1');
    await page.waitForFunction(() => document.getElementById('viewer').dataset.loaded === 'true', { timeout: 120000 });
    assert.match(await page.$eval('#measure', el => el.textContent), /× 130\.5 cm/, 'the fake agent made it taller');
    assert.match(await page.$eval('#log', el => el.textContent), /9 attached image\(s\)/);
    assert.equal(await page.$eval('#revise', el => el.hidden), true);
    assert.equal(await page.$eval('#reviseStart', el => el.hidden), false);
    assert.equal(await page.$eval('#history', el => el.children.length), 2);
    await step(page, '027-revised');
    // Leaving the paint mode gives the left button back to orbiting.
    const left = await page.evaluate(() => window.glbsmith.viewer.controls.mouseButtons.LEFT);
    assert.equal(left, 0);

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
    await page.waitForFunction(() => document.querySelectorAll('#history li').length === 3);
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
    assert.equal(await page.$eval('#history', el => el.children.length), 3);
    assert.deepEqual(errors, []);
    console.log('UI smoke test passed');
} finally {
    await browser.close();
}
