// "Make changes" on a finished model: paint it in the 3D view, up to five colours,
// each with its own instruction, then start a revision job.

import { t } from './i18n.js';
import { clipboardImages } from './clipboard.js';
import { ModelPicker } from './models.js';

const $ = id => document.getElementById(id);
const COLOURS = ['#e6194b', '#2f80ed', '#27ae60', '#f2a900', '#9b51e0'];

let context = null; // { viewer, agents, job, submit }
let rows = [];      // { id, colour, prompt, images: [{ file, url }] }
let active = null;
let generalImages = [];

function attach(list, files, redraw) {
    const images = [...files].filter(file => file.type.startsWith('image/'));
    if (!images.length) return;
    $('reviseError').hidden = true;
    for (const file of images) list.push({ file, url: URL.createObjectURL(file) });
    redraw();
}

/** Thumbnails of what is attached to one instruction, an attach button and a drop target. */
function fillAttachments(host, list, redraw) {
    const input = Object.assign(document.createElement('input'), { type: 'file', accept: 'image/*', multiple: true, hidden: true });
    input.addEventListener('change', () => { attach(list, input.files, redraw); input.value = ''; });
    const button = Object.assign(document.createElement('button'), { type: 'button', textContent: t('revise.attach'), title: t('revise.attachHelp') });
    button.addEventListener('click', () => input.click());
    host.replaceChildren(...list.map(image => {
        const box = Object.assign(document.createElement('span'), { className: 'attached' });
        const remove = Object.assign(document.createElement('button'), { type: 'button', textContent: '×' });
        remove.setAttribute('aria-label', t('revise.removeImage'));
        remove.addEventListener('click', () => {
            URL.revokeObjectURL(image.url);
            list.splice(list.indexOf(image), 1);
            redraw();
        });
        box.append(Object.assign(document.createElement('img'), { src: image.url, alt: image.file.name }), remove);
        return box;
    }), button, input);
}

const renderGeneral = () => fillAttachments($('generalAttachments'), generalImages, renderGeneral);
for (const [type, listener] of [
    ['dragover', event => { event.preventDefault(); $('generalAttachments').classList.add('over'); }],
    ['dragleave', () => $('generalAttachments').classList.remove('over')],
    ['drop', event => {
        event.preventDefault();
        $('generalAttachments').classList.remove('over');
        attach(generalImages, event.dataTransfer.files, renderGeneral);
    }],
]) for (const area of [$('generalAttachments'), $('reviseGeneral')]) area.addEventListener(type, listener);

const asDataUrl = file => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
});
const encode = list => Promise.all(list.map(image => asDataUrl(image.file)));
const modelPicker = new ModelPicker({
    select: $('reviseModelSelect'), customField: $('reviseModelCustom'), custom: $('reviseModel'), agent: () => $('reviseAgent').value,
});
$('reviseAgent').addEventListener('change', () => modelPicker.load(''));

function setTool(tool) {
    context.viewer.setPaintTool(tool);
    for (const button of document.querySelectorAll('[data-revise-tool]')) button.setAttribute('aria-pressed', String(button.dataset.reviseTool === tool));
}

function select(row) {
    active = row;
    context.viewer.setPaintColour(row.id, row.colour);
    renderRows();
}

function addRow() {
    const used = new Set(rows.map(row => row.id));
    const id = COLOURS.findIndex((_, index) => !used.has(index));
    if (id < 0) return;
    const row = { id, colour: COLOURS[id], prompt: '', images: [] };
    rows.push(row);
    select(row);
    setTool('paint');
}

function renderRows() {
    // Redrawing replaces the text boxes: stay in the one being typed in, so pasting doesn't interrupt.
    const typing = rows.findIndex((_, index) => $('colours').children[index]?.querySelector('textarea') === document.activeElement);
    const caret = typing >= 0 ? document.activeElement.selectionStart : 0;
    $('colours').replaceChildren(...rows.map((row, index) => {
        const item = document.createElement('li');
        const swatch = Object.assign(document.createElement('button'), { type: 'button', className: 'swatch' });
        swatch.style.background = row.colour;
        swatch.setAttribute('aria-pressed', String(row === active));
        swatch.setAttribute('aria-label', t('revise.colourLabel', { n: index + 1 }));
        swatch.addEventListener('click', () => { select(row); setTool('paint'); });
        const prompt = Object.assign(document.createElement('textarea'), { rows: 2, maxLength: 4000, value: row.prompt, placeholder: t('revise.promptPlaceholder') });
        prompt.setAttribute('aria-label', t('revise.colourPrompt'));
        prompt.addEventListener('input', () => { row.prompt = prompt.value; });
        prompt.addEventListener('focus', () => { if (row !== active) { active = row; context.viewer.setPaintColour(row.id, row.colour); for (const [i, b] of [...$('colours').querySelectorAll('.swatch')].entries()) b.setAttribute('aria-pressed', String(rows[i] === row)); } });
        const comment = Object.assign(document.createElement('div'), { className: 'comment' });
        const attachments = Object.assign(document.createElement('div'), { className: 'attachments' });
        fillAttachments(attachments, row.images, renderRows);
        comment.append(prompt, attachments);
        // The whole instruction takes dropped images, text box included.
        comment.addEventListener('dragover', event => { event.preventDefault(); attachments.classList.add('over'); });
        comment.addEventListener('dragleave', () => attachments.classList.remove('over'));
        comment.addEventListener('drop', event => {
            event.preventDefault();
            attachments.classList.remove('over');
            attach(row.images, event.dataTransfer.files, renderRows);
        });
        item.append(swatch, comment);
        if (rows.length > 1) {
            const remove = Object.assign(document.createElement('button'), { type: 'button', className: 'remove', textContent: '×' });
            remove.setAttribute('aria-label', t('revise.removeColour'));
            remove.addEventListener('click', () => {
                rows.splice(rows.indexOf(row), 1);
                context.viewer.removeColour(row.id);
                select(rows.includes(active) ? active : rows[0]);
            });
            item.append(remove);
        }
        return item;
    }));
    $('addColour').hidden = rows.length >= COLOURS.length;
    if (typing >= 0) {
        const box = $('colours').children[typing]?.querySelector('textarea');
        box?.focus();
        box?.setSelectionRange(caret, caret);
    }
}

function renderAgents() {
    const select = $('reviseAgent');
    select.replaceChildren(...context.agents.map(agent => Object.assign(document.createElement('option'), { value: agent.id, textContent: agent.label })));
    // Starts from what made this model.
    const sameAgent = context.agents.some(agent => agent.id === context.job.agent);
    if (sameAgent) select.value = context.job.agent;
    const started = context.job.model;
    modelPicker.load('').then(() => { if (context && sameAgent && started) modelPicker.choose(started); });
}

export function openRevise(next) {
    if (context) return;
    context = next;
    rows = [];
    active = null;
    generalImages = [];
    renderGeneral();
    context.viewer.startPaint();
    $('revise').hidden = false;
    $('result').classList.add('revising');
    $('reviseStart').hidden = true;
    $('reviseError').hidden = true;
    $('reviseGeneral').value = '';
    $('reviseBrush').value = 25;
    renderAgents();
    addRow();
    $('result').scrollIntoView({ block: 'start' });
}

export function closeRevise() {
    if (!context) return;
    context.viewer.stopPaint();
    context = null;
    $('revise').hidden = true;
    $('result').classList.remove('revising');
}

export function relabelRevise() {
    if (context) { renderRows(); renderGeneral(); modelPicker.render(); }
}

// Pasting: into the instruction being typed, or else the colour in use.
addEventListener('paste', event => {
    if (!context) return;
    const images = clipboardImages(event);
    if (event.target === $('reviseGeneral')) attach(generalImages, images, renderGeneral);
    else if (active) attach(active.images, images, renderRows);
});

for (const button of document.querySelectorAll('[data-revise-tool]')) button.addEventListener('click', () => setTool(button.dataset.reviseTool));
$('reviseBrush').addEventListener('input', e => context?.viewer.setBrush(Number(e.target.value)));
$('reviseUndo').addEventListener('click', () => context?.viewer.undoPaint());
$('addColour').addEventListener('click', addRow);
$('reviseCancel').addEventListener('click', () => { closeRevise(); $('reviseStart').hidden = false; });
$('reviseSubmit').addEventListener('click', async () => {
    const error = $('reviseError');
    error.hidden = true;
    const fail = text => { error.textContent = text; error.hidden = false; };
    const requests = [];
    for (const [index, row] of rows.entries()) {
        const points = context.viewer.marks(row.id);
        const prompt = row.prompt.trim();
        if (points.length && !prompt) return fail(t('revise.needPrompt', { n: index + 1 }));
        if (prompt && !points.length) return fail(t('revise.needPaint', { n: index + 1 }));
        if (points.length) requests.push({ color: row.colour, prompt, points, images: row.images });
    }
    const general = $('reviseGeneral').value.trim();
    if (!requests.length && !general) return fail(t('revise.nothing'));
    $('reviseSubmit').disabled = true;
    try {
        for (const request of requests) request.images = await encode(request.images);
        await context.submit({ requests, general, general_images: await encode(generalImages), agent: $('reviseAgent').value, model: modelPicker.value() || null, view: context.viewer.screenshot() });
    } catch (failure) {
        fail(failure.message);
    } finally {
        $('reviseSubmit').disabled = false;
    }
});
