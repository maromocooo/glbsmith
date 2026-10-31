const messages = {
    ja: {
        tagline: '商品写真と寸法から、AI エージェントが Blender で実寸の GLB を作ります', language: '言語',
        drop: '商品の写真をドロップ、貼り付け、またはクリックで選択（複数可）', mainPhoto: 'メイン', makeMain: 'メインにする', removePhoto: '写真を外す',
        paintButton: '塗りつぶしで指定', paintAgain: '塗り直す', tooManyPhotos: '写真は {n} 枚までです',
        'paint.title': '対象のオブジェクトを塗る', 'paint.help': '作りたい商品だけを塗ってください。多少はみ出しても大丈夫です。輪郭を描いて「塗りつぶし」で中をクリックすると一度に塗れます。',
        'paint.brush': 'ブラシ', 'paint.eraser': '消しゴム', 'paint.fill': '塗りつぶし', 'paint.size': '太さ', 'paint.undo': '元に戻す', 'paint.clear': '全部消す', 'paint.cancel': 'キャンセル', 'paint.save': '保存',
        concurrency: '同時に作る数', concurrencyOption: '{n} 個', load: '実行中 {running} ・ 順番待ち {queued}', name: '名前（任意）', namePlaceholder: '例: フロアランプ',
        width: '幅', depth: '奥行き', height: '高さ', agent: 'エージェント', model: 'モデル', modelName: 'モデル名', modelDefault: 'CLI の既定', modelDefaultNamed: '設定どおり（{name}）', modelOther: 'その他（名前を入力）',
        noBlender: 'Blender が見つかりません。BLENDER_BIN を設定してください。',
        noSandbox: 'このシステムではサンドボックスが使えません。エージェントのコードが PC のファイルに触れる可能性を理解した上で実行する',
        create: 'オブジェクトを作成', quotaNote: '選んだエージェントのサブスク枠を消費します（同時に作ると、そのぶん速く減ります）。写真はそのエージェントのサービスに送られます。',
        history: '履歴', noHistory: 'まだありません', cancel: 'キャンセル', download: 'GLB をダウンロード',
        photo: '写真', result: '結果（ドラッグで回転）', renders: '途中経過', log: 'エージェントのログ',
        empty: '写真と寸法を入れて「オブジェクトを作成」を押すと、ここに進捗と結果が出ます', pickImage: '写真を選んでください',
        pickAgent: 'エージェントを選んでください', unavailable: '（未インストール）', untitled: '名前なし', waitingModel: '完成するとここに 3D で表示します',
        modelFailed: 'モデルを読み込めませんでした', size: '実寸 {w} × {d} × {h} cm（幅 × 奥行き × 高さ）',
        'status.queued': '順番待ち', 'status.running': '作成中', 'status.done': '完成', 'status.failed': '失敗', 'status.cancelled': 'キャンセルしました', 'status.unknown': '不明',
        'tool.get_brief': '写真と寸法を確認中', 'tool.run_python': 'モデリング中', 'tool.render_views': 'レンダリングして写真と比較中',
        'tool.measure': '寸法を計測中', 'tool.bake_materials': '材質を焼き込み中', 'tool.finalize': '寸法を検査して書き出し中', 'tool.check_against_photo': '写真に重ねて比率を確認中',
        sizeNote: '写真の比率を優先したので、入力より小さくなりました: {items}。入力した寸法が正しいか確認してください。', 'axis.width_x': '幅', 'axis.depth_y': '奥行き', 'axis.height_z': '高さ',
        checkFailed: '写真との比率チェックが合格しないまま完成しました（重なり {overlap}）。写真と見比べて確認してください。', checkUnreliable: 'このメイン写真は背景が単色でないため、比率を自動で測れませんでした。塗りつぶしで対象を指定すると測れるようになります。', 
        'options.open': 'オプション', 'options.title': 'オプション', 'options.close': '閉じる',
        'options.help': 'API キーを設定すると、エージェントに「(API)」の選択肢が増えます。サブスクの選択肢はこれまでどおりで、キーは使いません。キーはこの PC の自分だけが読めるファイルに平文で保存され、選んだジョブにだけ渡します。',
        'options.anthropic': 'Anthropic API キー（Claude Code 用）', 'options.openai': 'OpenAI API キー（Codex 用）',
        'options.notSet': '未設定', 'options.saved': '保存済み（…{end}）', 'options.fromEnv': '環境変数 {name} から（…{end}）',
        'options.save': '保存', 'options.remove': '削除', 'options.keyPlaceholder': 'キーを貼り付け',
        'options.billingNote': 'API キーの従量課金です。サブスク枠ではなく、使った分だけ請求されます。',
        elapsed: '{s} 秒', tokens: '{n} トークン', tokenDetail: '合計 {total} トークン（入力 {input}、うちキャッシュ {cached} ／ 出力 {output}）',
        'revise.open': '修正を加える', 'revise.title': 'モデルに修正を加える',
        'revise.help': '直したい場所を色で塗り、色ごとに指示を書いてください。左ドラッグで塗る、右ドラッグで回転、ホイールでズームです。元のモデルは残ります。',
        'revise.paint': '塗る', 'revise.view': '視点を動かす', 'revise.addColour': '色を追加',
        'revise.colourPrompt': 'この色の部分をどう直しますか', 'revise.promptPlaceholder': '例: もっと長くしてください', 'revise.removeColour': 'この色を削除', 'revise.colourLabel': '色 {n} を選ぶ',
        'revise.general': '全体への指示（任意）', 'revise.generalPlaceholder': '例: 全体を今の1.5倍の大きさにしてください',
        'revise.attach': '画像を添付', 'revise.removeImage': '画像を外す', 'revise.attachHelp': '画像は何枚でも、指示の欄にドロップ、貼り付け（Ctrl+V）、またはボタンで添付できます。貼り付けは、選択中の色か入力中の指示に付きます。画像はエージェントのサービスに送られます。',
        'revise.submit': '修正開始', 'revise.needPrompt': '色 {n} は塗ってありますが、指示がありません', 'revise.needPaint': '色 {n} は指示がありますが、塗ってありません',
        'revise.nothing': '色を塗って指示を書くか、全体への指示を入れてください', revision: '修正 {n}',
    },
    en: {
        tagline: 'An AI agent models a true-to-size GLB in Blender from a product photo and its dimensions', language: 'Language',
        drop: 'Drop, paste or click to choose product photos (several is fine)', mainPhoto: 'Main', makeMain: 'Make main', removePhoto: 'Remove photo',
        paintButton: 'Paint the item', paintAgain: 'Repaint', tooManyPhotos: 'At most {n} photos',
        'paint.title': 'Paint the item', 'paint.help': "Paint only the product you want. It's fine to go over the edges a little. Draw an outline, then click inside with Fill to paint it all at once.",
        'paint.brush': 'Brush', 'paint.eraser': 'Eraser', 'paint.fill': 'Fill', 'paint.size': 'Size', 'paint.undo': 'Undo', 'paint.clear': 'Clear', 'paint.cancel': 'Cancel', 'paint.save': 'Save',
        concurrency: 'How many to make at once', concurrencyOption: '{n}', load: 'Running {running} · Waiting {queued}', name: 'Name (optional)', namePlaceholder: 'e.g. Floor lamp',
        width: 'Width', depth: 'Depth', height: 'Height', agent: 'Agent', model: 'Model', modelName: 'Model name', modelDefault: "The CLI's default", modelDefaultNamed: 'As configured ({name})', modelOther: 'Other (type a name)',
        noBlender: 'Blender was not found. Set BLENDER_BIN.',
        noSandbox: "The sandbox isn't available on this system. Run anyway, understanding that the agent's code could reach files on this PC",
        create: 'Create object', quotaNote: "This uses your subscription quota for the chosen agent (faster when making several at once), and the photos are sent to that agent's service.",
        history: 'History', noHistory: 'Nothing yet', cancel: 'Cancel', download: 'Download GLB',
        photo: 'Photo', result: 'Result (drag to rotate)', renders: 'Progress', log: 'Agent log',
        empty: 'Add a photo and its size, then press Create object. Progress and the result appear here', pickImage: 'Choose a photo',
        pickAgent: 'Choose an agent', unavailable: '(not installed)', untitled: 'Untitled', waitingModel: 'The 3D model appears here when it is ready',
        modelFailed: "The model couldn't be loaded", size: 'Real size {w} × {d} × {h} cm (width × depth × height)',
        'status.queued': 'Waiting', 'status.running': 'Working', 'status.done': 'Done', 'status.failed': 'Failed', 'status.cancelled': 'Cancelled', 'status.unknown': 'Unknown',
        'tool.get_brief': 'Reading the photo and size', 'tool.run_python': 'Modelling', 'tool.render_views': 'Rendering and comparing with the photo',
        'tool.measure': 'Measuring', 'tool.bake_materials': 'Baking materials', 'tool.finalize': 'Checking the size and writing the file', 'tool.check_against_photo': 'Laying the model over the photo to check proportions',
        sizeNote: 'The proportions in the photo took priority, so this came out smaller than you entered: {items}. Check the size you entered.', 'axis.width_x': 'width', 'axis.depth_y': 'depth', 'axis.height_z': 'height',
        checkFailed: "This finished without passing the check against the photo (overlap {overlap}). Compare it with the photo yourself.", checkUnreliable: "This main photo's background isn't plain, so its proportions couldn't be measured automatically. Painting the item makes that possible.", 
        'options.open': 'Options', 'options.title': 'Options', 'options.close': 'Close',
        'options.help': 'With an API key set, an "(API)" choice appears among the agents. The subscription choices stay as they are and do not use the key. Keys are saved in plain text in a file only you can read on this PC, and go only to the jobs you choose them for.',
        'options.anthropic': 'Anthropic API key (for Claude Code)', 'options.openai': 'OpenAI API key (for Codex)',
        'options.notSet': 'Not set', 'options.saved': 'Saved (…{end})', 'options.fromEnv': 'From the environment variable {name} (…{end})',
        'options.save': 'Save', 'options.remove': 'Remove', 'options.keyPlaceholder': 'Paste the key',
        'options.billingNote': 'Billed per use on your API key, not from your subscription.',
        elapsed: '{s} s', tokens: '{n} tokens', tokenDetail: '{total} tokens in total (input {input}, of which cached {cached}; output {output})',
        'revise.open': 'Make changes', 'revise.title': 'Change the model',
        'revise.help': 'Paint what you want changed, and write an instruction for each colour. Left-drag paints, right-drag rotates, and the wheel zooms. The original model is kept.',
        'revise.paint': 'Paint', 'revise.view': 'Move the view', 'revise.addColour': 'Add colour',
        'revise.colourPrompt': 'How should this colour change?', 'revise.promptPlaceholder': 'e.g. Make this longer', 'revise.removeColour': 'Remove this colour', 'revise.colourLabel': 'Choose colour {n}',
        'revise.general': 'About the whole model (optional)', 'revise.generalPlaceholder': 'e.g. Make the whole thing 1.5 times as big',
        'revise.attach': 'Attach image', 'revise.removeImage': 'Remove image', 'revise.attachHelp': "Attach any number of images to an instruction: drop them on it, paste with Ctrl+V, or use the button. Pasted images go to the instruction you're typing in, or the chosen colour. Images are sent to the agent's service.",
        'revise.submit': 'Start changes', 'revise.needPrompt': 'Colour {n} is painted but has no instruction', 'revise.needPaint': 'Colour {n} has an instruction but no paint',
        'revise.nothing': 'Paint a colour and write an instruction, or add one about the whole model', revision: 'Revision {n}',
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
