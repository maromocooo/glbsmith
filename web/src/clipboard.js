// Images on the clipboard, however they got there. A screenshot or a copied file
// shows up in `files`; some sources only offer it through `items`.

export function clipboardImages(event) {
    const data = event.clipboardData;
    if (!data) return [];
    const files = [...data.files].filter(file => file.type.startsWith('image/'));
    if (files.length) return files;
    return [...data.items]
        .filter(item => item.kind === 'file' && item.type.startsWith('image/'))
        .map(item => item.getAsFile())
        .filter(Boolean);
}
