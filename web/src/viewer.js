// Shows a finished GLB with a box around it and the size read from the file itself.

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

export class Viewer {
    constructor(host) {
        this.host = host;
        this.loader = new GLTFLoader();
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
        return { width: size.x, height: size.y, depth: size.z };
    }
}
