const messages = {
    ja: {
        tagline: '商品写真と寸法から、AI エージェントが Blender で実寸の GLB を作ります', language: '言語',
        drop: '商品の写真をドロップ、貼り付け、またはクリックで選択', name: '名前（任意）', namePlaceholder: '例: フロアランプ',
        width: '幅', depth: '奥行き', height: '高さ', agent: 'エージェント', model: 'モデル', modelName: 'モデル名', modelDefault: 'CLI の既定', modelDefaultNamed: '設定どおり（{name}）', modelOther: 'その他（名前を入力）',
        noBlender: 'Blender が見つかりません。BLENDER_BIN を設定してください。',
        noSandbox: 'このシステムではサンドボックスが使えません。エージェントのコードが PC のファイルに触れる可能性を理解した上で実行する',
        create: 'オブジェクトを作成', quotaNote: '選んだエージェントのサブスク枠を消費します。写真はそのエージェントのサービスに送られます。',
        history: '履歴', noHistory: 'まだありません', cancel: 'キャンセル', download: 'GLB をダウンロード',
        photo: '写真', result: '結果（ドラッグで回転）', renders: '途中経過', log: 'エージェントのログ',
        empty: '写真と寸法を入れて「オブジェクトを作成」を押すと、ここに進捗と結果が出ます', pickImage: '写真を選んでください',
        pickAgent: 'エージェントを選んでください', unavailable: '（未インストール）', untitled: '名前なし', waitingModel: '完成するとここに 3D で表示します',
        modelFailed: 'モデルを読み込めませんでした', size: '実寸 {w} × {d} × {h} cm（幅 × 奥行き × 高さ）',
        'status.queued': '順番待ち', 'status.running': '作成中', 'status.done': '完成', 'status.failed': '失敗', 'status.cancelled': 'キャンセルしました', 'status.unknown': '不明',
        'tool.get_brief': '写真と寸法を確認中', 'tool.run_python': 'モデリング中', 'tool.render_views': 'レンダリングして写真と比較中',
        'tool.measure': '寸法を計測中', 'tool.bake_materials': '材質を焼き込み中', 'tool.finalize': '寸法を検査して書き出し中', 'tool.check_against_photo': '写真に重ねて比率を確認中',
        sizeNote: '写真の比率を優先したので、入力より小さくなりました: {items}。入力した寸法が正しいか確認してください。', 'axis.width_x': '幅', 'axis.depth_y': '奥行き', 'axis.height_z': '高さ',
        checkFailed: '写真との比率チェックが合格しないまま完成しました（重なり {overlap}）。写真と見比べて確認してください。', checkUnreliable: 'この写真は背景が単色でないため、比率を自動で測れませんでした。', 
        elapsed: '{s} 秒',
    },
    en: {
        tagline: 'An AI agent models a true-to-size GLB in Blender from a product photo and its dimensions', language: 'Language',
        drop: 'Drop, paste or click to choose a product photo', name: 'Name (optional)', namePlaceholder: 'e.g. Floor lamp',
        width: 'Width', depth: 'Depth', height: 'Height', agent: 'Agent', model: 'Model', modelName: 'Model name', modelDefault: "The CLI's default", modelDefaultNamed: 'As configured ({name})', modelOther: 'Other (type a name)',
        noBlender: 'Blender was not found. Set BLENDER_BIN.',
        noSandbox: "The sandbox isn't available on this system. Run anyway, understanding that the agent's code could reach files on this PC",
        create: 'Create object', quotaNote: "This uses your subscription quota for the chosen agent, and the photo is sent to that agent's service.",
        history: 'History', noHistory: 'Nothing yet', cancel: 'Cancel', download: 'Download GLB',
        photo: 'Photo', result: 'Result (drag to rotate)', renders: 'Progress', log: 'Agent log',
        empty: 'Add a photo and its size, then press Create object. Progress and the result appear here', pickImage: 'Choose a photo',
        pickAgent: 'Choose an agent', unavailable: '(not installed)', untitled: 'Untitled', waitingModel: 'The 3D model appears here when it is ready',
        modelFailed: "The model couldn't be loaded", size: 'Real size {w} × {d} × {h} cm (width × depth × height)',
        'status.queued': 'Waiting', 'status.running': 'Working', 'status.done': 'Done', 'status.failed': 'Failed', 'status.cancelled': 'Cancelled', 'status.unknown': 'Unknown',
        'tool.get_brief': 'Reading the photo and size', 'tool.run_python': 'Modelling', 'tool.render_views': 'Rendering and comparing with the photo',
        'tool.measure': 'Measuring', 'tool.bake_materials': 'Baking materials', 'tool.finalize': 'Checking the size and writing the file', 'tool.check_against_photo': 'Laying the model over the photo to check proportions',
        sizeNote: 'The proportions in the photo took priority, so this came out smaller than you entered: {items}. Check the size you entered.', 'axis.width_x': 'width', 'axis.depth_y': 'depth', 'axis.height_z': 'height',
        checkFailed: "This finished without passing the check against the photo (overlap {overlap}). Compare it with the photo yourself.", checkUnreliable: "This photo's background isn't plain, so its proportions couldn't be measured automatically.", 
        elapsed: '{s} s',
    },
};

const KEY = 'glbsmith.language';
function stored() { try { return localStorage.getItem(KEY); } catch { return null; } }
let language = stored() || (navigator.language.startsWith('ja') ? 'ja' : 'en');

export const getLanguage = () => language;
export function setLanguage(next) {
    language = next;
    try { localStorage.setItem(KEY, next); } catch { /* not remembered */ }
    apply();
}
export function t(key, values = {}) {
    const text = messages[language][key] ?? key;
    return text.replace(/\{(\w+)\}/g, (_, name) => values[name] ?? '');
}
export function apply() {
    document.documentElement.lang = language;
    for (const el of document.querySelectorAll('[data-i18n]')) el.textContent = t(el.dataset.i18n);
    for (const el of document.querySelectorAll('[data-i18n-placeholder]')) el.placeholder = t(el.dataset.i18nPlaceholder);
}
export const allMessages = messages;
