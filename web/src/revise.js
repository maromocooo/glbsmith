// "Make changes" on a finished model: paint it in the 3D view, up to five colours,
// each with its own instruction, then start a revision job.

import { t } from './i18n.js';
import { ModelPicker } from './models.js';

const $ = id => document.getElementById(id);
const COLOURS = ['#e6194b', '#2f80ed', '#27ae60', '#f2a900', '#9b51e0'];

let context = null; // { viewer, agents, job, submit }
let rows = [];      // { id, colour, prompt }
let active = null;
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
    const row = { id, colour: COLOURS[id], prompt: '' };
    rows.push(row);
    select(row);
    setTool('paint');
}

function renderRows() {
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
        item.append(swatch, prompt);
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
    if (context) { renderRows(); modelPicker.render(); }
}

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
        if (points.length) requests.push({ color: row.colour, prompt, points });
    }
    const general = $('reviseGeneral').value.trim();
    if (!requests.length && !general) return fail(t('revise.nothing'));
    $('reviseSubmit').disabled = true;
    try {
        await context.submit({ requests, general, agent: $('reviseAgent').value, model: modelPicker.value() || null, view: context.viewer.screenshot() });
    } catch (failure) {
        fail(failure.message);
    } finally {
        $('reviseSubmit').disabled = false;
    }
});
