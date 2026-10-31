// The options dialog: API keys. The page only ever learns whether a key is set
// and how it ends, never the key itself.

import { t } from './i18n.js';

const $ = id => document.getElementById(id);
const PROVIDERS = ['anthropic', 'openai'];

export function setupOptions({ onChange }) {
    let described = null;

    function stateText(info) {
        if (!info.set) return t('options.notSet');
        return info.source === 'saved' ? t('options.saved', { end: info.ends_with ?? '' }) : t('options.fromEnv', { name: info.environment, end: info.ends_with ?? '' });
    }

    function render() {
        $('keyRows').replaceChildren(...PROVIDERS.map(provider => {
            const info = described[provider];
            const row = Object.assign(document.createElement('div'), { className: 'key-row' });
            row.dataset.provider = provider;
            const title = Object.assign(document.createElement('strong'), { textContent: t(`options.${provider}`) });
            const state = Object.assign(document.createElement('small'), { className: 'note', textContent: stateText(info) });
            const inputs = Object.assign(document.createElement('div'), { className: 'inputs' });
            const input = Object.assign(document.createElement('input'), { type: 'password', autocomplete: 'off', placeholder: t('options.keyPlaceholder'), maxLength: 500 });
            input.setAttribute('aria-label', t(`options.${provider}`));
            const save = Object.assign(document.createElement('button'), { type: 'button', className: 'primary', textContent: t('options.save') });
            save.addEventListener('click', () => { if (input.value.trim()) store(provider, input.value); });
            inputs.append(input, save);
            if (info.source === 'saved') {
                const remove = Object.assign(document.createElement('button'), { type: 'button', textContent: t('options.remove') });
                remove.addEventListener('click', () => store(provider, ''));
                inputs.append(remove);
            }
            row.append(title, state, inputs);
            return row;
        }));
    }

    async function store(provider, key) {
        $('optionsError').hidden = true;
        const response = await fetch(`/api/keys/${provider}`, {
            method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ key }),
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) {
            $('optionsError').textContent = typeof body.detail === 'string' ? body.detail : response.statusText;
            $('optionsError').hidden = false;
            return;
        }
        described = body;
        render();
        await onChange();
    }

    $('optionsOpen').addEventListener('click', async () => {
        described = await (await fetch('/api/keys')).json();
        $('optionsError').hidden = true;
        render();
        $('options').showModal();
    });
    $('optionsClose').addEventListener('click', () => $('options').close());

    return { relabel() { if (described) render(); } };
}
