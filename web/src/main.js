import { apply, getLanguage, setLanguage, t } from './i18n.js';
import { Viewer } from './viewer.js';

const $ = id => document.getElementById(id);
const viewer = new Viewer($('viewer'));
let photo = null;
let status = null;
let currentId = null;
let source = null;

async function api(path, options) {
    const response = await fetch(path, options);
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || response.statusText);
    return body;
}

// ---- form ----
function setPhoto(file) {
    if (!file?.type.startsWith('image/')) return;
    photo = file;
    $('preview').src = URL.createObjectURL(file);
    $('preview').hidden = false;
    $('dropHint').hidden = true;
}
$('image').addEventListener('change', e => setPhoto(e.target.files[0]));
$('dropzone').addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('image').click(); } });
for (const type of ['dragenter', 'dragover']) $('dropzone').addEventListener(type, e => { e.preventDefault(); $('dropzone').classList.add('over'); });
for (const type of ['dragleave', 'drop']) $('dropzone').addEventListener(type, e => { e.preventDefault(); $('dropzone').classList.remove('over'); });
$('dropzone').addEventListener('drop', e => setPhoto(e.dataTransfer.files[0]));
addEventListener('paste', e => {
    const file = [...e.clipboardData.files].find(f => f.type.startsWith('image/'));
    if (file) setPhoto(file);
});

// ---- model choice: what each agent's CLI says is available ----
const OTHER = '__other__';
const modelLists = new Map();
const selectedAgent = () => document.querySelector('input[name=agent]:checked')?.value;

function renderModels() {
    const select = $('modelSelect');
    const previous = select.value;
    const list = modelLists.get(selectedAgent()) ?? { default: null, options: [] };
    const option = (value, label) => Object.assign(document.createElement('option'), { value, textContent: label });
    select.replaceChildren(
        option('', list.default ? t('modelDefaultNamed', { name: list.default }) : t('modelDefault')),
        ...list.options.map(m => option(m.id, m.label === m.id ? m.id : `${m.label} (${m.id})`)),
        option(OTHER, t('modelOther')),
    );
    if ([...select.options].some(o => o.value === previous)) select.value = previous;
    $('modelCustom').hidden = select.value !== OTHER;
}

async function loadModels(agent) {
    if (agent && !modelLists.has(agent)) {
        try {
            modelLists.set(agent, await api(`/api/agents/${agent}/models`));
        } catch {
            return; // the default and typing a name still work
        }
    }
    if (agent === selectedAgent()) renderModels();
}

$('modelSelect').addEventListener('change', () => { $('modelCustom').hidden = $('modelSelect').value !== OTHER; });
$('agents').addEventListener('change', () => { $('modelSelect').value = ''; renderModels(); loadModels(selectedAgent()); });

function renderAgents() {
    const host = $('agents');
    const chosen = host.querySelector('input:checked')?.value;
    host.replaceChildren(...status.agents.map(agent => {
        const label = document.createElement('label');
        label.classList.toggle('off', !agent.available);
        const input = Object.assign(document.createElement('input'), { type: 'radio', name: 'agent', value: agent.id, disabled: !agent.available });
        const text = document.createElement('span');
        text.textContent = agent.available ? agent.label : `${agent.label} ${t('unavailable')}`;
        label.append(input, text);
        return label;
    }));
    const pick = host.querySelector(`input[value="${chosen}"]`) ?? host.querySelector('input:not(:disabled)');
    if (pick && !pick.disabled) pick.checked = true;
    renderModels();
    loadModels(selectedAgent());
}

$('form').addEventListener('submit', async e => {
    e.preventDefault();
    const error = $('formError');
    error.hidden = true;
    const agent = document.querySelector('input[name=agent]:checked')?.value;
    const problem = !photo ? t('pickImage') : !agent ? t('pickAgent') : null;
    if (problem) { error.textContent = problem; error.hidden = false; return; }
    const form = new FormData();
    form.set('image', photo);
    for (const id of ['width', 'depth', 'height', 'name']) form.set(id, $(id).value);
    form.set('model', $('modelSelect').value === OTHER ? $('model').value.trim() : $('modelSelect').value);
    form.set('agent', agent);
    form.set('allow_unsandboxed', $('allowUnsandboxed').checked ? 'true' : 'false');
    $('submit').disabled = true;
    try {
        const job = await api('/api/jobs', { method: 'POST', body: form });
        await refreshHistory();
        show(job.id);
    } catch (failure) {
        error.textContent = failure.message;
        error.hidden = false;
    } finally {
        $('submit').disabled = false;
    }
});

// ---- history ----
let history = [];
async function refreshHistory() {
    history = await api('/api/jobs');
    renderHistory();
}
function renderHistory() {
    const list = $('history');
    if (!history.length) {
        const empty = document.createElement('li');
        empty.className = 'note';
        empty.textContent = t('noHistory');
        list.replaceChildren(empty);
        return;
    }
    list.replaceChildren(...history.map(job => {
        const item = document.createElement('li');
        const button = document.createElement('button');
        button.type = 'button';
        button.setAttribute('aria-current', String(job.id === currentId));
        button.addEventListener('click', () => show(job.id));
        const image = Object.assign(document.createElement('img'), { src: `/api/jobs/${job.id}/reference`, alt: '', loading: 'lazy' });
        const text = document.createElement('span');
        text.textContent = job.brief.name || t('untitled');
        const detail = document.createElement('small');
        detail.textContent = [t(`status.${job.status}`), job.agent, job.model].filter(Boolean).join(' · ');
        text.append(detail);
        button.append(image, text);
        item.append(button);
        return item;
    }));
}

// ---- result ----
function placeholder(text) {
    const host = $('viewer');
    host.querySelector('.placeholder')?.remove();
    if (!text) return;
    const note = Object.assign(document.createElement('div'), { className: 'placeholder', textContent: text });
    host.append(note);
}

function addRender(path) {
    const name = path.split('/').pop();
    const url = `/api/jobs/${currentId}/renders/${name}`;
    if ($('renders').querySelector(`[data-name="${name}"]`)) return;
    const link = Object.assign(document.createElement('a'), { href: url, target: '_blank', rel: 'noopener' });
    link.dataset.name = name;
    link.append(Object.assign(document.createElement('img'), { src: url, alt: '', loading: 'lazy' }));
    $('renders').append(link);
    $('rendersTitle').hidden = false;
}

function addLog(text) {
    $('log').append(Object.assign(document.createElement('li'), { textContent: text }));
}

function onEvent(event) {
    if (event.kind === 'tool') {
        const key = `tool.${event.name}`;
        const label = t(key);
        if (label !== key) $('status').textContent = label;
        addLog(event.name);
    } else if (event.kind === 'text') {
        addLog(event.text);
    } else if (event.kind === 'render' || event.kind === 'overlay') {
        addRender(event.path);
    } else if (event.kind === 'error') {
        addLog(event.text);
    }
}

async function renderState(job) {
    const running = job.status === 'queued' || job.status === 'running';
    $('cancel').hidden = !running;
    $('status').classList.toggle('failed', job.status === 'failed');
    if (!running || !$('status').textContent) {
        let text = t(`status.${job.status}`);
        if (job.seconds) text += ` · ${t('elapsed', { s: job.seconds })}`;
        if (job.error) text += ` · ${job.error}`;
        $('status').textContent = text;
    }
    $('message').textContent = job.message ?? '';
    const smaller = Object.entries(job.report?.smaller_than_box ?? {});
    $('sizeNote').hidden = !job.has_model || !smaller.length;
    $('sizeNote').textContent = t('sizeNote', {
        items: smaller.map(([axis, v]) => `${t(`axis.${axis}`)} ${v.box_cm} → ${v.model_cm} cm`).join(', '),
    });
    const check = job.report?.photo_check;
    const verdict = job.has_model ? check?.verdict : null;
    $('checkNote').hidden = verdict !== 'fail' && verdict !== 'unreliable';
    $('checkNote').textContent = verdict === 'fail' ? t('checkFailed', { overlap: check.overlap }) : t('checkUnreliable');
    if (job.has_model) {
        const url = `/api/jobs/${job.id}/model.glb`;
        const download = $('download');
        download.href = url;
        download.download = `${(job.brief.name || 'model').replace(/[^\p{L}\p{N} _-]/gu, '_')}.glb`;
        download.hidden = false;
        placeholder(null);
        try {
            const size = await viewer.load(url);
            if (currentId !== job.id) return;
            const cm = v => (v * 100).toFixed(1);
            $('measure').textContent = t('size', { w: cm(size.width), d: cm(size.depth), h: cm(size.height) });
            $('viewer').dataset.loaded = 'true';
        } catch {
            placeholder(t('modelFailed'));
        }
    } else {
        placeholder(running ? t('waitingModel') : t(`status.${job.status}`));
    }
    await refreshHistory();
}

function show(id) {
    currentId = id;
    source?.close();
    viewer.clear();
    $('empty').hidden = true;
    $('result').hidden = false;
    $('download').hidden = true;
    $('measure').textContent = '';
    $('message').textContent = '';
    $('sizeNote').hidden = $('checkNote').hidden = true;
    $('status').textContent = '';
    $('renders').replaceChildren();
    $('rendersTitle').hidden = true;
    $('log').replaceChildren();
    delete $('viewer').dataset.loaded;
    $('reference').src = `/api/jobs/${id}/reference`;
    const job = history.find(j => j.id === id);
    $('resultTitle').textContent = job?.brief.name || t('untitled');
    renderHistory();
    source = new EventSource(`/api/jobs/${id}/events`);
    source.onmessage = message => { if (id === currentId) onEvent(JSON.parse(message.data)); };
    source.addEventListener('state', message => {
        const state = JSON.parse(message.data);
        if (id !== currentId) return;
        $('resultTitle').textContent = state.brief.name || t('untitled');
        renderState(state);
        if (state.status !== 'queued' && state.status !== 'running') source.close();
    });
}

$('cancel').addEventListener('click', () => api(`/api/jobs/${currentId}/cancel`, { method: 'POST' }));

// ---- start ----
$('language').value = getLanguage();
$('language').addEventListener('change', e => { setLanguage(e.target.value); renderAgents(); renderHistory(); });
apply();
status = await api('/api/status');
renderAgents();
$('noBlender').hidden = status.blender;
$('noSandbox').hidden = status.sandbox;
$('submit').disabled = !status.blender;
await refreshHistory();
window.glbsmith = { viewer };
