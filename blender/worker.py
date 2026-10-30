"""Runs inside Blender (`blender -b --python worker.py -- <job_dir> <unsandboxed?>`).

Reads one JSON command per line on stdin and answers with one line starting
with MARKER on stdout (Blender prints its own output there too). Standard
library and bpy only.

Conventions: 1 unit = 1 metre. X = width, Y = depth, Z = height, and the
front of the item faces -Y, which glTF export turns into +Z with +Y up.
"""

import contextlib
import io
import json
import math
import os
import sys
import traceback
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).parent))
import sandbox  # noqa: E402

MARKER = "@@GLBSMITH "
TOLERANCE = 0.02       # finalize corrects up to this much per axis, refuses beyond it
RIG = "__glbsmith_rig__"
RENDER_SIZE = 512

argv = sys.argv[sys.argv.index("--") + 1:]
JOB = Path(argv[0]).resolve()
ALLOW_UNSANDBOXED = len(argv) > 1 and argv[1] == "unsandboxed"
BRIEF = json.loads((JOB / "brief.json").read_text())
TARGET = (BRIEF["width"] / 100, BRIEF["depth"] / 100, BRIEF["height"] / 100)  # metres


def lock_down() -> dict:
    blender_dir = str(Path(bpy.app.binary_path).resolve().parent)
    read = [blender_dir, "/usr", "/lib", "/lib64", "/bin", "/sys/devices/system/cpu", "/sys/devices/system/node",
            "/proc", "/etc/ld.so.cache", "/etc/fonts", "/etc/localtime", "/dev/urandom", "/dev/random"]
    try:
        abi = sandbox.restrict(read=read, write=[str(JOB)], write_files=["/dev/null", "/dev/zero"])
    except sandbox.SandboxUnavailable as error:
        if not ALLOW_UNSANDBOXED:
            raise
        return {"sandbox": False, "reason": str(error)}
    sandbox.block_python_escapes()
    return {"sandbox": True, "landlock_abi": abi}


# ---- scene helpers ----
def model_objects():
    return [o for o in bpy.context.scene.objects
            if o.type in {"MESH", "CURVE", "SURFACE", "FONT", "META"}
            and not o.name.startswith(RIG) and o.visible_get() and not o.hide_render]


def evaluated_bounds(obj, depsgraph):
    """World-space (min, max) of an object with modifiers applied, or None if it has no geometry."""
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        if not mesh.vertices:
            return None, 0, 0
        matrix = evaluated.matrix_world
        points = [matrix @ v.co for v in mesh.vertices]
        low = Vector(min(p[i] for p in points) for i in range(3))
        high = Vector(max(p[i] for p in points) for i in range(3))
        return (low, high), len(mesh.vertices), len(mesh.polygons)
    finally:
        evaluated.to_mesh_clear()


def scene_bounds():
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    low, high = None, None
    parts = []
    for obj in model_objects():
        bounds, vertices, faces = evaluated_bounds(obj, depsgraph)
        if not bounds:
            continue
        low = bounds[0] if low is None else Vector(map(min, low, bounds[0]))
        high = bounds[1] if high is None else Vector(map(max, high, bounds[1]))
        parts.append((obj, bounds, vertices, faces))
    return low, high, parts


def cm(vector):
    return [round(v * 100, 2) for v in vector]


# ---- commands ----
def cmd_run_python(code: str, namespace={}):  # noqa: B006 - one namespace for the whole session
    namespace.setdefault("bpy", bpy)
    out = io.StringIO()
    error = None
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            exec(compile(code, "<agent>", "exec"), namespace)  # noqa: S102 - the point of the tool; see sandbox.py
        except Exception:  # noqa: BLE001
            error = traceback.format_exc(limit=4)
    save()
    return {"output": out.getvalue()[-8000:], "error": error}


def cmd_measure():
    low, high, parts = scene_bounds()
    if low is None:
        return {"overall": None, "parts": [], "target_cm": cm(TARGET)}
    size = high - low
    return {
        "target_cm": {"width_x": BRIEF["width"], "depth_y": BRIEF["depth"], "height_z": BRIEF["height"]},
        "overall_cm": {"width_x": round(size.x * 100, 2), "depth_y": round(size.y * 100, 2), "height_z": round(size.z * 100, 2)},
        "error_percent": {axis: round((size[i] / TARGET[i] - 1) * 100, 2) for i, axis in enumerate(("width_x", "depth_y", "height_z"))},
        "lowest_z_cm": round(low.z * 100, 2),
        "parts": [{
            "name": obj.name,
            "size_cm": cm(bounds[1] - bounds[0]),
            "centre_cm": cm((bounds[0] + bounds[1]) / 2),
            "vertices": vertices,
            "faces": faces,
            "materials": [slot.material.name for slot in obj.material_slots if slot.material],
        } for obj, bounds, vertices, faces in parts],
    }


VIEWS = {
    "front": (Vector((0, -1, 0)), (math.pi / 2, 0, 0)),
    "right": (Vector((1, 0, 0)), (math.pi / 2, 0, math.pi / 2)),
    "back": (Vector((0, 1, 0)), (math.pi / 2, 0, math.pi)),
    "left": (Vector((-1, 0, 0)), (math.pi / 2, 0, -math.pi / 2)),
    "top": (Vector((0, 0, 1)), (0, 0, 0)),
    "bottom": (Vector((0, 0, -1)), (math.pi, 0, 0)),
}
ANGLED = Vector((-0.62, -0.72, 0.42)).normalized()


def _hex_colour(text):
    value = text.lstrip("#")
    return tuple((int(value[i:i + 2], 16) / 255) ** 2.2 for i in (0, 2, 4))


def add_marks(collection):
    """The person's paint as glowing dots in the render. Returns what to delete afterwards."""
    created = []
    for index, request in enumerate(load_marks()):
        if not request["points"]:
            continue
        builder = bmesh.new()
        for x, y, z, radius in request["points"]:
            bmesh.ops.create_icosphere(builder, subdivisions=1, radius=radius, matrix=Matrix.Translation((x, y, z)))
        mesh = bpy.data.meshes.new(f"{RIG}mark{index}")
        builder.to_mesh(mesh)
        builder.free()
        material = bpy.data.materials.new(f"{RIG}mark{index}")
        material.use_nodes = True
        nodes = material.node_tree.nodes
        nodes.clear()
        emission = nodes.new("ShaderNodeEmission")
        emission.inputs["Color"].default_value = (*_hex_colour(request["color"]), 1)
        emission.inputs["Strength"].default_value = 1.0
        material.node_tree.links.new(emission.outputs[0], nodes.new("ShaderNodeOutputMaterial").inputs[0])
        mesh.materials.append(material)
        collection.objects.link(bpy.data.objects.new(f"{RIG}mark{index}", mesh))
        created.append((mesh, material))
    return created


def build_rig(show_marks=False):
    scene = bpy.context.scene
    collection = bpy.data.collections.new(RIG)
    scene.collection.children.link(collection)

    def add(name, data):
        obj = bpy.data.objects.new(f"{RIG}{name}", data)
        collection.objects.link(obj)
        return obj

    world = bpy.data.worlds.new(RIG)
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.72, 0.72, 0.72, 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.75
    saved_world = scene.world
    scene.world = world

    sun = bpy.data.lights.new(f"{RIG}sun", "SUN")
    sun.energy = 2.2
    sun.angle = math.radians(25)
    add("sun", sun).rotation_euler = (math.radians(50), 0, math.radians(-35))

    camera = bpy.data.cameras.new(f"{RIG}camera")
    camera_object = add("camera", camera)
    scene.camera = camera_object
    marks = add_marks(collection) if show_marks else []

    def remove():
        scene.world = saved_world
        scene.camera = None
        for obj in list(collection.objects):
            data = obj.data
            bpy.data.objects.remove(obj)
            if isinstance(data, bpy.types.Light):
                bpy.data.lights.remove(data)
            elif isinstance(data, bpy.types.Camera):
                bpy.data.cameras.remove(data)
        for mesh, material in marks:
            bpy.data.meshes.remove(mesh)
            bpy.data.materials.remove(material)
        bpy.data.collections.remove(collection)
        bpy.data.worlds.remove(world)

    return camera_object, camera, remove


def limit_threads(scene):
    """Parallel jobs share the CPU; the job queue decides each one's share."""
    threads = BRIEF.get("render_threads")
    if threads:
        scene.render.threads_mode = "FIXED"
        scene.render.threads = threads


def cmd_render_views(views=None, focus=None, label="render", show_marks=False, focus_box=None):
    """Render fixed views to PNGs. Orthographic views share one scale so proportions can be compared."""
    views = views or ["front", "right", "top", "angled"]
    unknown = [v for v in views if v != "angled" and v not in VIEWS]
    if unknown:
        return {"error": f"Unknown views: {unknown}. Use {[*VIEWS, 'angled']}."}
    low, high, parts = scene_bounds()
    if low is None:
        return {"error": "Nothing to render yet."}
    if focus:
        matching = [p for p in parts if p[0].name == focus]
        if not matching:
            return {"error": f"No visible object named {focus!r}."}
        low, high = matching[0][1]
    if focus_box:
        low, high = Vector(focus_box[0]), Vector(focus_box[1])
    centre = (low + high) / 2
    size = high - low
    extent = max(size.x, size.y, size.z, 0.01)

    scene = bpy.context.scene
    render = scene.render
    render.engine = "CYCLES"
    limit_threads(scene)
    scene.cycles.device = "CPU"
    scene.cycles.samples = 24
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = 4
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    render.resolution_x = render.resolution_y = RENDER_SIZE
    render.film_transparent = False
    render.image_settings.file_format = "PNG"

    camera_object, camera, remove = build_rig(show_marks)
    results = []
    try:
        (JOB / "renders").mkdir(exist_ok=True)
        for view in views:
            distance = extent * 3 + 1
            if view == "angled":
                camera.type = "PERSP"
                camera.lens = 50
                distance = extent * 2.6
                camera_object.location = centre + ANGLED * distance
                camera_object.rotation_mode = "QUATERNION"
                camera_object.rotation_quaternion = (-ANGLED).to_track_quat("-Z", "Y")
            else:
                direction, euler = VIEWS[view]
                camera.type = "ORTHO"
                camera.ortho_scale = extent * 1.12
                camera_object.location = centre + direction * distance
                camera_object.rotation_mode = "XYZ"
                camera_object.rotation_euler = euler
            camera.clip_end = distance * 4
            path = JOB / "renders" / f"{label}_{view}.png"
            render.filepath = str(path)
            with contextlib.redirect_stdout(io.StringIO()):
                bpy.ops.render.render(write_still=True)
            results.append({"view": view, "path": str(path), "image_width_cm": None if view == "angled" else round(extent * 112, 1)})
    finally:
        remove()
    save()
    return {"views": results, "size_cm": cm(size)}


def _base_colour(material):
    """Approximate sRGB colour of a material, for the flat preview in the photo check."""
    if not material:
        return (0.8, 0.8, 0.8)
    colour = material.diffuse_color[:3]
    if material.use_nodes:
        bsdf = next((n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf:
            socket = bsdf.inputs["Base Color"]
            colour = socket.default_value[:3]
            image = next((n.image for n in material.node_tree.nodes if n.type == "TEX_IMAGE" and n.image), None)
            if socket.links and image and image.size[0]:
                pixels = np.empty(len(image.pixels), dtype=np.float32)
                image.pixels.foreach_get(pixels)
                colour = pixels.reshape(-1, image.channels)[::97, :3].mean(axis=0)
    return tuple(float(max(0.0, c)) ** (1 / 2.2) for c in colour)


def cmd_export_triangles():
    """Write the model's world-space triangles to a file for the photo check."""
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    triangles, colours, parts, names = [], [], [], []
    for obj in model_objects():
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            count = len(mesh.loop_triangles)
            if not count:
                continue
            vertices = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
            mesh.vertices.foreach_get("co", vertices)
            vertices = vertices.reshape(-1, 3)
            matrix = np.array(evaluated.matrix_world)
            vertices = vertices @ matrix[:3, :3].T + matrix[:3, 3]
            indices = np.empty(count * 3, dtype=np.int32)
            mesh.loop_triangles.foreach_get("vertices", indices)
            slots = np.empty(count, dtype=np.int32)
            mesh.loop_triangles.foreach_get("material_index", slots)
            palette = [_base_colour(s.material) for s in obj.material_slots] or [_base_colour(None)]
            triangles.append(vertices[indices].reshape(-1, 3, 3))
            colours.append(np.array(palette)[np.minimum(slots, len(palette) - 1)])
            parts.append(np.full(count, len(names)))
            names.append(obj.name)
        finally:
            evaluated.to_mesh_clear()
    if not triangles:
        return {"error": "The scene has no geometry."}
    path = JOB / "tmp" / "triangles.npz"
    np.savez(path, triangles=np.concatenate(triangles), colours=np.concatenate(colours), parts=np.concatenate(parts), names=np.array(names))
    return {"path": str(path), "count": int(sum(len(t) for t in triangles))}


def _procedural_materials():
    """Materials whose colour comes from nodes glTF cannot export; they would come out flat."""
    bad = []
    for obj in model_objects():
        for slot in obj.material_slots:
            material = slot.material
            if not material or not material.use_nodes:
                continue
            for node in material.node_tree.nodes:
                if node.type != "BSDF_PRINCIPLED":
                    continue
                link = node.inputs["Base Color"].links
                if link and link[0].from_node.type not in {"TEX_IMAGE", "MIX", "MIX_RGB"} and material.name not in bad:
                    bad.append(material.name)
    return bad


def cmd_bake_materials(size=1024):
    """Bake procedural base colours to image textures, one per object and material."""
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    limit_threads(scene)
    scene.cycles.device = "CPU"
    scene.cycles.samples = 8
    baked = []
    for obj in [o for o in model_objects() if o.type == "MESH"]:
        for index, slot in enumerate(obj.material_slots):
            material = slot.material
            if not material or material.name not in _procedural_materials():
                continue
            if material.users > 1:  # shared: each object needs its own image and UVs
                material = material.copy()
                slot.material = material
            bpy.ops.object.select_all(action="DESELECT")
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            if not obj.data.uv_layers:
                bpy.ops.object.mode_set(mode="EDIT")
                bpy.ops.mesh.select_all(action="SELECT")
                bpy.ops.uv.smart_project(island_margin=0.02)
                bpy.ops.object.mode_set(mode="OBJECT")
            image = bpy.data.images.new(f"{obj.name}_{material.name}_baked", size, size)
            nodes = material.node_tree.nodes
            texture = nodes.new("ShaderNodeTexImage")
            texture.image = image
            for node in nodes:
                node.select = False
            texture.select = True
            nodes.active = texture
            obj.active_material_index = index
            with contextlib.redirect_stdout(io.StringIO()):
                bpy.ops.object.bake(type="DIFFUSE", pass_filter={"COLOR"}, margin=8)
            image.pack()
            bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
            material.node_tree.links.new(texture.outputs["Color"], bsdf.inputs["Base Color"])
            baked.append({"object": obj.name, "material": material.name, "image": image.name})
    save()
    return {"baked": baked, "still_procedural": _procedural_materials()}


# ---- revisions ----
MARKS = JOB / "marks.json"


def load_marks():
    return json.loads(MARKS.read_text()) if MARKS.exists() else []


def prepare_marks():
    """Put the person's paint into this scene's coordinates, once, before anything is edited.

    The paint was done on the GLB, which finalize centred on its base, in glTF axes,
    and may have scaled by up to 2%. The saved scene still has the original position.
    """
    source = JOB / "revision.json"
    if MARKS.exists() or not source.exists():
        return
    revision = json.loads(source.read_text())
    low, high, _ = scene_bounds()
    size = high - low
    glb = revision["glb_size_cm"]
    scale = [glb["width"] / 100 / size.x, glb["depth"] / 100 / size.y, glb["height"] / 100 / size.z]
    centre = (low + high) / 2
    requests = []
    for request in revision["requests"]:
        points = [[x / scale[0] + centre.x, -z / scale[1] + centre.y, y / scale[2] + low.z, radius]
                  for x, y, z, radius in request["points"]]
        requests.append({"color": request["color"], "prompt": request["prompt"], "points": points})
    MARKS.write_text(json.dumps(requests))


def cmd_revision_marks():
    """Where each colour was painted, and which parts it landed on."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    objects = []
    for obj in model_objects():
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        vertices = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
        mesh.vertices.foreach_get("co", vertices)
        matrix = np.array(evaluated.matrix_world)
        evaluated.to_mesh_clear()
        if len(vertices):
            objects.append((obj.name, vertices.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]))
    results = []
    for request in load_marks():
        points = np.array(request["points"], dtype=np.float64).reshape(-1, 4)
        entry = {"color": request["color"], "prompt": request["prompt"], "painted_spots": len(points)}
        if len(points):
            sample = points[np.linspace(0, len(points) - 1, min(len(points), 60)).astype(int), :3]
            nearest = {}
            for point in sample:
                name = min(objects, key=lambda o: np.min(np.sum((o[1] - point) ** 2, axis=1)))[0]
                nearest[name] = nearest.get(name, 0) + 1
            low, high = points[:, :3].min(axis=0), points[:, :3].max(axis=0)
            reach = points[:, 3].max()
            entry.update({
                "painted_from_cm": cm(low), "painted_to_cm": cm(high),
                "box": [(low - reach * 3).tolist(), (high + reach * 3).tolist()],
                "parts": [{"name": name, "share": round(count / len(sample), 2)}
                          for name, count in sorted(nearest.items(), key=lambda item: -item[1])],
            })
        results.append(entry)
    return {"requests": results}


def cmd_finalize():
    low, high, parts = scene_bounds()
    if low is None:
        return {"ok": False, "reason": "The scene has no geometry."}
    size = high - low
    ratios = [size[i] / TARGET[i] for i in range(3)]
    axes = ("width_x", "depth_y", "height_z")
    measured = {a: round(size[i] * 100, 2) for i, a in enumerate(axes)}
    target = dict(zip(axes, (BRIEF["width"], BRIEF["depth"], BRIEF["height"])))
    percent = {a: round((ratios[i] - 1) * 100, 2) for i, a in enumerate(axes)}
    # The given size is the box the item must fit in. It has to touch the box on
    # at least one axis, which fixes its scale; the photo decides the rest.
    # A revision follows the person's instructions instead, and is written as built.
    revision = bool(BRIEF.get("revision"))
    largest = 1.0 if revision else max(ratios)
    if largest > 1 + TOLERANCE:
        return {"ok": False, "reason": f"The model is more than {TOLERANCE:.0%} bigger than the box it must fit in. "
                                       "Scale the whole model down so its proportions stay as they are.",
                "measured_cm": measured, "box_cm": target, "error_percent": percent}
    if largest < 1 - TOLERANCE:
        return {"ok": False, "reason": "The model does not reach the box on any axis, so its scale is wrong. Scale the whole "
                                       "model up until the axis closest to the box touches it.",
                "measured_cm": measured, "box_cm": target, "error_percent": percent}

    # Axes that nearly touch the box snap to it exactly; the rest follow the
    # scale of the largest axis, so a small correction never changes proportions.
    uniform = 1 / largest
    scales = [1.0] * 3 if revision else [1 / ratios[i] if abs(ratios[i] - 1) <= TOLERANCE else uniform for i in range(3)]

    # Bake every part into flat world-space meshes: exact size, no shear from
    # scaling rotated children, origin at the centre of the base.
    centre = (low + high) / 2
    fit = Matrix.Diagonal((*scales, 1)) @ Matrix.Translation((-centre.x, -centre.y, -low.z))
    depsgraph = bpy.context.evaluated_depsgraph_get()
    collection = bpy.data.collections.new("__glbsmith_export__")
    bpy.context.scene.collection.children.link(collection)
    exported = []
    renamed = []
    try:
        for obj, *_ in parts:
            evaluated = obj.evaluated_get(depsgraph)
            mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=depsgraph)
            matrix = fit @ evaluated.matrix_world
            mesh.transform(matrix)
            if matrix.determinant() < 0:
                mesh.flip_normals()
            # Free the name for the copy, so the GLB keeps "lamp_shade" and not "lamp_shade.001".
            name = obj.name
            obj.name = f"__glbsmith_original__{name}"
            renamed.append((obj, name))
            copy = bpy.data.objects.new(name, mesh)
            collection.objects.link(copy)
            exported.append(copy)

        bpy.ops.object.select_all(action="DESELECT")
        for copy in exported:
            copy.select_set(True)
        bpy.context.view_layer.objects.active = exported[0]
        output = JOB / "model.glb"
        with contextlib.redirect_stdout(io.StringIO()):
            bpy.ops.export_scene.gltf(
                filepath=str(output), export_format="GLB", use_selection=True, export_apply=False, export_yup=True,
                export_cameras=False, export_lights=False, export_animations=False, export_materials="EXPORT",
            )
    finally:
        for copy in exported:
            mesh = copy.data
            bpy.data.objects.remove(copy)
            bpy.data.meshes.remove(mesh)
        bpy.data.collections.remove(collection)
        for obj, name in renamed:
            obj.name = name
    save()
    return {
        "ok": True,
        "path": str(output),
        "measured_before_correction_cm": measured,
        "correction_percent": {a: round((scales[i] - 1) * 100, 2) for i, a in enumerate(axes)},
        "smaller_than_box": {} if revision else {a: {"box_cm": target[a], "model_cm": round(size[i] * scales[i] * 100, 1)}
                                                 for i, a in enumerate(axes) if ratios[i] * scales[i] < 1 - TOLERANCE},
        "expected_cm": {a: round(size[i] * scales[i] * 100, 3) for i, a in enumerate(axes)},
        "faces": sum(p[3] for p in parts),
        "parts": len(parts),
        "flat_materials_warning": _procedural_materials(),
    }


COMMANDS = {"run_python": cmd_run_python, "measure": cmd_measure, "render_views": cmd_render_views, "bake_materials": cmd_bake_materials, "export_triangles": cmd_export_triangles,
            "revision_marks": cmd_revision_marks, "finalize": cmd_finalize}


def save():
    with contextlib.redirect_stdout(io.StringIO()):
        bpy.ops.wm.save_as_mainfile(filepath=str(JOB / "model.blend"), copy=True, check_existing=False)


def reply(message: dict) -> None:
    sys.__stdout__.write(MARKER + json.dumps(message) + "\n")
    sys.__stdout__.flush()


def main() -> None:
    os.chdir(JOB)
    # Continue an interrupted session if there is one, else start empty.
    saved = JOB / "model.blend"
    if saved.exists():
        bpy.ops.wm.open_mainfile(filepath=str(saved))
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.unit_settings.system = "METRIC"
    bpy.context.scene.unit_settings.length_unit = "METERS"
    bpy.context.scene.unit_settings.scale_length = 1.0

    state = lock_down()
    prepare_marks()
    reply({"ready": True, **state, "blender": bpy.app.version_string})

    for line in sys.stdin:
        try:
            request = json.loads(line)
            result = COMMANDS[request["command"]](**request.get("args", {}))
            reply({"id": request.get("id"), "result": result})
        except Exception:  # noqa: BLE001 - report it and keep the session alive
            reply({"id": None, "error": traceback.format_exc(limit=4)})


main()
