// A model dropdown that follows the chosen agent: what its CLI offers, its default,
// and "other" for typing a name. Used when creating and when making changes.

import { t } from './i18n.js';

const OTHER = '__other__';
const lists = new Map(); // agent id -> { default, options }; asking the CLI takes a moment, so keep it

async function listFor(agent) {
    if (!lists.has(agent)) {
        const response = await fetch(`/api/agents/${agent}/models`);
        if (!response.ok) throw new Error(response.statusText);
        lists.set(agent, await response.json());
    }
    return lists.get(agent);
}

export class ModelPicker {
    constructor({ select, customField, custom, agent }) {
        Object.assign(this, { select, customField, custom, agent });
        select.addEventListener('change', () => this._showCustom());
    }

    _showCustom() { this.customField.hidden = this.select.value !== OTHER; }

    /** Redraw for the current agent, keeping the choice if it is still on offer. */
    render(wanted = this.select.value) {
        const list = lists.get(this.agent()) ?? { default: null, options: [] };
        const option = (value, label) => Object.assign(document.createElement('option'), { value, textContent: label });
        this.select.replaceChildren(
            option('', list.default ? t('modelDefaultNamed', { name: list.default }) : t('modelDefault')),
            ...list.options.map(m => option(m.id, m.label === m.id ? m.id : `${m.label} (${m.id})`)),
            option(OTHER, t('modelOther')),
        );
        if ([...this.select.options].some(o => o.value === wanted)) this.select.value = wanted;
        this._showCustom();
    }

    /** Fetch the list for the current agent, then show it. Without one, the default and typing still work. */
    async load(wanted) {
        const agent = this.agent();
        this.render(wanted);
        if (!agent) return;
        try {
            await listFor(agent);
        } catch {
            return;
        }
        // The person may have chosen something while the list was on its way; that wins.
        if (agent === this.agent()) this.render(this.select.value || wanted);
    }

    /** The model name to send: empty means the CLI's default. */
    value() { return this.select.value === OTHER ? this.custom.value.trim() : this.select.value; }

    /** Select a model that came from elsewhere: from the list, or typed in if the list doesn't have it. */
    choose(name) {
        const known = [...this.select.options].some(o => o.value === name);
        this.select.value = known ? name : OTHER;
        if (!known) this.custom.value = name;
        this._showCustom();
    }
}
