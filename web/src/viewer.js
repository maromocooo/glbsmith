// Shows a finished GLB with a box around it and the size read from the file itself.

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

const SPOTS_PER_COLOUR = 4000;

export class Viewer {
    constructor(host) {
        this.host = host;
        this.loader = new GLTFLoader();
        this.paint = null;
        this._raycaster = new THREE.Raycaster();
    }

    _init() {
        if (this.renderer) return;
        const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
        renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
        renderer.toneMapping = THREE.NeutralToneMapping;
        this.host.appendChild(renderer.domElement);
        this.renderer = renderer;
        this.scene = new THREE.Scene();
        const pmrem = new THREE.PMREMGenerator(renderer);
        this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
        pmrem.dispose();
        const sun = new THREE.DirectionalLight(0xffffff, 1.6);
        sun.position.set(-2, 4, 3);
        this.scene.add(sun);
        this.camera = new THREE.PerspectiveCamera(40, 1, 0.01, 200);
        this.controls = new OrbitControls(this.camera, renderer.domElement);
        this.controls.enableDamping = true;
        this.controls.addEventListener('change', () => this._render());
        new ResizeObserver(() => this._resize()).observe(this.host);
        this._resize();
        renderer.setAnimationLoop(() => {
            if (this.host.isConnected && this.controls.update()) this._render();
        });
    }

    _resize() {
        const { clientWidth: w, clientHeight: h } = this.host;
        if (!w || !h) return;
        this.renderer.setSize(w, h);
        this.camera.aspect = w / h;
        this.camera.updateProjectionMatrix();
        this._render();
    }

    _render() { this.renderer.render(this.scene, this.camera); }

    clear() {
        this.stopPaint();
        if (!this.content) return;
        this.scene.remove(this.content, this.box, this.grid);
        this.content.traverse(o => {
            if (!o.isMesh) return;
            o.geometry.dispose();
            for (const m of [o.material].flat()) { m.map?.dispose(); m.dispose(); }
        });
        this.grid.geometry.dispose();
        this.content = null;
        this._render();
    }

    /** Load a GLB; resolves to its real size in metres, as { width, depth, height }. */
    async load(url) {
        this._init();
        this.clear();
        const gltf = await this.loader.loadAsync(url);
        this.content = gltf.scene;
        this.scene.add(this.content);
        const bounds = new THREE.Box3().setFromObject(this.content);
        const size = bounds.getSize(new THREE.Vector3());
        const centre = bounds.getCenter(new THREE.Vector3());
        this.box = new THREE.Box3Helper(bounds, 0x8089ee);
        const reach = Math.max(size.x, size.y, size.z);
        this.grid = new THREE.GridHelper(reach * 2.4, 12, 0x999999, 0xbbbbbb);
        this.grid.material.transparent = true;
        this.grid.material.opacity = 0.5;
        this.scene.add(this.box, this.grid);
        // Starts from the front-left, the item's front facing +z.
        const distance = reach / (2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov) / 2)) * 1.5;
        this.camera.position.copy(centre).add(new THREE.Vector3(-0.5, 0.35, 0.8).normalize().multiplyScalar(distance));
        this.controls.target.copy(centre);
        this.controls.update();
        this._render();
        this.reach = reach;
        return { width: size.x, height: size.y, depth: size.z };
    }

    // ---- painting on the model ----
    // Paint is a list of spots on the surface: [x, y, z, radius] in metres, in the GLB's
    // own coordinates. That is what the agent gets, so it stays exact on thin parts
    // whatever the mesh looks like, which painting on triangles would not.
    startPaint() {
        if (this.paint || !this.content) return;
        const dom = this.renderer.domElement;
        this.paint = { tool: 'paint', active: 0, brush: 25, layers: new Map(), undo: [], stroke: false };
        this._savedButtons = { ...this.controls.mouseButtons };
        this.preview = new THREE.Mesh(new THREE.SphereGeometry(1, 16, 12),
            new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.45, depthTest: false }));
        this.preview.visible = false;
        this.scene.add(this.preview);
        this.setPaintTool('paint');
        this._paintListeners = [
            ['pointerdown', e => this._paintDown(e)],
            ['pointermove', e => this._paintMove(e)],
            ['pointerup', () => { if (this.paint) this.paint.stroke = false; }],
            ['pointerleave', () => { if (this.preview) { this.preview.visible = false; this._render(); } }],
        ];
        for (const [type, listener] of this._paintListeners) dom.addEventListener(type, listener);
    }

    stopPaint() {
        if (!this.paint) return;
        const dom = this.renderer.domElement;
        for (const [type, listener] of this._paintListeners) dom.removeEventListener(type, listener);
        for (const layer of this.paint.layers.values()) {
            this.scene.remove(layer.mesh);
            layer.mesh.geometry.dispose();
            layer.mesh.material.dispose();
        }
        this.scene.remove(this.preview);
        this.preview.geometry.dispose();
        this.preview.material.dispose();
        this.preview = null;
        Object.assign(this.controls.mouseButtons, this._savedButtons);
        dom.style.cursor = '';
        this.paint = null;
        this._render();
    }

    /** 'paint' and 'erase' use the left button; 'view' turns it back into orbiting. Right-drag always orbits. */
    setPaintTool(tool) {
        this.paint.tool = tool;
        this.controls.mouseButtons.LEFT = tool === 'view' ? THREE.MOUSE.ROTATE : null;
        this.controls.mouseButtons.RIGHT = THREE.MOUSE.ROTATE;
        this.renderer.domElement.style.cursor = tool === 'view' ? 'grab' : 'crosshair';
    }

    setPaintColour(id, hex) {
        this.paint.active = id;
        this._layer(id, hex);
        this.preview.material.color.set(hex);
    }

    setBrush(value) { this.paint.brush = value; }

    removeColour(id) {
        const layer = this.paint.layers.get(id);
        if (!layer) return;
        this.scene.remove(layer.mesh);
        layer.mesh.geometry.dispose();
        layer.mesh.material.dispose();
        this.paint.layers.delete(id);
        this.paint.undo = this.paint.undo.filter(snapshot => !(id in snapshot));
        this._render();
    }

    undoPaint() {
        const snapshot = this.paint.undo.pop();
        if (!snapshot) return;
        for (const [id, spots] of Object.entries(snapshot)) {
            const layer = this.paint.layers.get(Number(id));
            if (layer) { layer.spots = spots; this._syncLayer(layer); }
        }
        this._render();
    }

    /** Spots painted with each colour id. */
    marks(id) { return this.paint?.layers.get(id)?.spots ?? []; }

    /** What the person is looking at, paint included. */
    screenshot() {
        this.preview.visible = false;
        this._render();
        return this.renderer.domElement.toDataURL('image/png');
    }

    _layer(id, hex) {
        if (this.paint.layers.has(id)) return this.paint.layers.get(id);
        const mesh = new THREE.InstancedMesh(new THREE.SphereGeometry(1, 12, 8),
            new THREE.MeshBasicMaterial({ color: hex, transparent: true, opacity: 0.75 }), SPOTS_PER_COLOUR);
        mesh.count = 0;
        mesh.frustumCulled = false;
        mesh.renderOrder = 2;
        this.scene.add(mesh);
        const layer = { mesh, spots: [] };
        this.paint.layers.set(id, layer);
        return layer;
    }

    _syncLayer(layer) {
        const matrix = new THREE.Matrix4();
        layer.spots.forEach(([x, y, z, r], index) => {
            matrix.makeScale(r, r, r).setPosition(x, y, z);
            layer.mesh.setMatrixAt(index, matrix);
        });
        layer.mesh.count = layer.spots.length;
        layer.mesh.instanceMatrix.needsUpdate = true;
    }

    _hit(event) {
        const rect = this.renderer.domElement.getBoundingClientRect();
        const ndc = new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
        this._raycaster.setFromCamera(ndc, this.camera);
        return this._raycaster.intersectObject(this.content, true)[0]?.point ?? null;
    }

    _radius() { return this.reach * (0.002 + this.paint.brush / 100 * 0.05); }

    _paintDown(event) {
        if (event.button !== 0 || this.paint.tool === 'view') return;
        const snapshot = {};
        const touched = this.paint.tool === 'erase' ? [...this.paint.layers.keys()] : [this.paint.active];
        for (const id of touched) snapshot[id] = [...(this.paint.layers.get(id)?.spots ?? [])];
        this.paint.undo.push(snapshot);
        if (this.paint.undo.length > 30) this.paint.undo.shift();
        this.paint.stroke = true;
        this.paint.last = null;
        this._paintAt(this._hit(event));
    }

    _paintMove(event) {
        if (this._paintQueued) return;
        // One raycast per frame: it walks every triangle.
        this._paintQueued = true;
        requestAnimationFrame(() => {
            this._paintQueued = false;
            if (!this.paint) return;
            const point = this.paint.tool === 'view' ? null : this._hit(event);
            if (this.paint.stroke) this._paintAt(point);
            this.preview.visible = Boolean(point);
            if (point) {
                this.preview.position.copy(point);
                this.preview.scale.setScalar(this._radius());
            }
            this._render();
        });
    }

    _paintAt(point) {
        if (!point) return;
        const radius = this._radius();
        const near = (spot, distance) => Math.hypot(spot[0] - point.x, spot[1] - point.y, spot[2] - point.z) < distance;
        if (this.paint.tool === 'erase') {
            for (const layer of this.paint.layers.values()) {
                const kept = layer.spots.filter(spot => !near(spot, radius));
                if (kept.length !== layer.spots.length) { layer.spots = kept; this._syncLayer(layer); }
            }
        } else {
            const layer = this.paint.layers.get(this.paint.active);
            // A fast drag jumps between frames, so fill the gap, unless it jumped to another part of the item.
            const last = this.paint.last;
            const gap = last ? last.distanceTo(point) : 0;
            const steps = last && gap < this.reach * 0.15 ? Math.ceil(gap / (radius * 0.6)) : 0;
            const add = at => {
                const close = spot => Math.hypot(spot[0] - at.x, spot[1] - at.y, spot[2] - at.z) < radius * 0.4;
                if (layer.spots.length < SPOTS_PER_COLOUR && !layer.spots.slice(-300).some(close)) layer.spots.push([at.x, at.y, at.z, radius]);
            };
            for (let i = 1; i < steps; i++) add(last.clone().lerp(point, i / steps));
            add(point);
            this.paint.last = point.clone();
            this._syncLayer(layer);
        }
        this._render();
    }
}
