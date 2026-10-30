// Modal for painting the product in a photo: brush, eraser and bucket fill.
// The paint layer has the photo's own resolution, and its alpha is the mask.

const UNDO_STEPS = 10;
const $ = id => document.getElementById(id);

const dialog = $('paint');
const photoCanvas = $('paintPhoto');
const layer = $('paintLayer');
const ink = layer.getContext('2d', { willReadFrequently: true });
const COLOUR = [255, 0, 150];

let tool = 'brush';
let undo = [];
let stroke = null;
let finish = null;

function setTool(next) {
    tool = next;
    for (const button of dialog.querySelectorAll('[data-paint-tool]')) button.setAttribute('aria-pressed', String(button.dataset.paintTool === tool));
}

function remember() {
    undo.push(ink.getImageData(0, 0, layer.width, layer.height));
    if (undo.length > UNDO_STEPS) undo.shift();
}

function point(event) {
    const rect = layer.getBoundingClientRect();
    return { x: (event.clientX - rect.left) * layer.width / rect.width, y: (event.clientY - rect.top) * layer.height / rect.height };
}

/** Brush width in image pixels: the slider is a share of the image's longer side. */
function brushWidth() {
    return Math.max(1, Number($('brushSize').value) / 400 * Math.max(layer.width, layer.height));
}

function drawTo(to) {
    ink.globalCompositeOperation = tool === 'eraser' ? 'destination-out' : 'source-over';
    ink.strokeStyle = ink.fillStyle = `rgb(${COLOUR.join(',')})`;
    ink.lineWidth = brushWidth();
    ink.lineCap = ink.lineJoin = 'round';
    ink.beginPath();
    ink.moveTo(stroke.x, stroke.y);
    ink.lineTo(to.x, to.y);
    ink.stroke();
    ink.globalCompositeOperation = 'source-over';
    stroke = to;
}

/** Fill the unpainted area around a point, stopping at painted pixels. */
function bucket({ x, y }) {
    const { width, height } = layer;
    const image = ink.getImageData(0, 0, width, height);
    const pixels = image.data;
    const empty = i => pixels[i * 4 + 3] < 128;
    const start = Math.floor(y) * width + Math.floor(x);
    if (start < 0 || start >= width * height || !empty(start)) return;
    const stack = [start];
    while (stack.length) {
        const i = stack.pop();
        if (!empty(i)) continue;
        pixels.set([...COLOUR, 255], i * 4);
        const column = i % width;
        if (column > 0) stack.push(i - 1);
        if (column < width - 1) stack.push(i + 1);
        if (i >= width) stack.push(i - width);
        if (i < width * (height - 1)) stack.push(i + width);
    }
    ink.putImageData(image, 0, 0);
}

layer.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    remember();
    const at = point(event);
    if (tool === 'fill') { bucket(at); return; }
    layer.setPointerCapture(event.pointerId);
    stroke = at;
    drawTo(at); // a click without moving still leaves a dot
});
layer.addEventListener('pointermove', event => { if (stroke) drawTo(point(event)); });
for (const type of ['pointerup', 'pointercancel']) layer.addEventListener(type, () => { stroke = null; });

for (const button of dialog.querySelectorAll('[data-paint-tool]')) button.addEventListener('click', () => setTool(button.dataset.paintTool));
$('paintUndo').addEventListener('click', () => { const last = undo.pop(); if (last) ink.putImageData(last, 0, 0); });
$('paintClear').addEventListener('click', () => { remember(); ink.clearRect(0, 0, layer.width, layer.height); });
$('paintCancel').addEventListener('click', () => dialog.close());
dialog.addEventListener('close', () => { finish?.(undefined); finish = null; });
$('paintSave').addEventListener('click', () => {
    const painted = ink.getImageData(0, 0, layer.width, layer.height).data.some((value, i) => i % 4 === 3 && value > 127);
    const done = finish;
    finish = null;
    if (!painted) { done?.(null); dialog.close(); return; }
    layer.toBlob(blob => { done?.(blob); dialog.close(); }, 'image/png');
});

/** Opens the modal. Resolves to a PNG mask, null if it was left blank, or undefined if cancelled. */
export function paintMask(photoUrl, existing) {
    return new Promise((resolve, reject) => {
        const image = new Image();
        image.onerror = reject;
        image.onload = async () => {
            for (const canvas of [photoCanvas, layer]) {
                canvas.width = image.naturalWidth;
                canvas.height = image.naturalHeight;
            }
            photoCanvas.getContext('2d').drawImage(image, 0, 0);
            ink.clearRect(0, 0, layer.width, layer.height);
            if (existing) ink.drawImage(await createImageBitmap(existing), 0, 0, layer.width, layer.height);
            undo = [];
            setTool('brush');
            finish = resolve;
            dialog.showModal();
        };
        image.src = photoUrl;
    });
}
