"""Real Blender, no agent: size checks, axes, GLB contents and the sandbox."""

import json
from pathlib import Path

import pytest
from PIL import Image

from glbsmith.blender_client import BlenderWorker, blender_binary
from glbsmith.glb import bounds

pytestmark = pytest.mark.skipif(not Path(blender_binary()).exists(), reason="Blender is not installed")

MODEL = """
import bpy
def box(name, size, location, colour):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = size
    bpy.ops.object.transform_apply(scale=True)
    material = bpy.data.materials.new(name + "_mat")
    material.use_nodes = True
    material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (*colour, 1)
    obj.data.materials.append(material)
    return obj
box("base", (W, D, 0.1), (0.3, 0.2, 0.05), (0.8, 0.1, 0.1))
box("post", (0.05, 0.05, H - 0.1), (0.3, 0.2, 0.1 + (H - 0.1) / 2), (0.1, 0.1, 0.8))
# A marker on the front of the item, which must end up on glTF +Z.
box("front_marker", (0.05, 0.02, 0.05), (0.3, 0.2 - D / 2 + 0.01, 0.2), (0.1, 0.8, 0.1))
"""


@pytest.fixture
def worker(tmp_path):
    def start(width=40, depth=30, height=120):
        Image.new("RGB", (8, 8), (255, 255, 255)).save(tmp_path / "photo.png")
        (tmp_path / "brief.json").write_text(json.dumps({"width": width, "depth": depth, "height": height, "image": "photo.png"}))
        w = BlenderWorker(tmp_path)
        w.start()
        started.append(w)
        return w

    started = []
    yield start
    for w in started:
        w.stop()


def build(worker, w=0.4, d=0.3, h=1.2):
    result = worker.call("run_python", code=f"W, D, H = {w}, {d}, {h}\n{MODEL}")
    assert result["error"] is None, result["error"]


def test_exact_model_exports_true_size_centred_on_its_base(worker, tmp_path):
    blender = worker()
    build(blender)
    assert blender.call("finalize")["ok"]
    glb = bounds(tmp_path / "model.glb")
    assert glb["size"] == pytest.approx([0.4, 1.2, 0.3], abs=1e-4)
    assert glb["min"][1] == pytest.approx(0, abs=1e-4)
    assert glb["min"][0] == pytest.approx(-glb["max"][0], abs=1e-4)
    assert glb["materials"] == 3


def test_small_error_is_corrected(worker, tmp_path):
    blender = worker()
    build(blender, h=1.2 * 1.015)
    result = blender.call("finalize")
    assert result["ok"] and result["correction_percent"]["height_z"] < 0
    assert bounds(tmp_path / "model.glb")["size"][1] == pytest.approx(1.2, abs=1e-4)


def test_large_error_is_refused(worker, tmp_path):
    blender = worker()
    build(blender, h=1.2 * 1.1)
    result = blender.call("finalize")
    assert not result["ok"] and "error_percent" in result
    assert not (tmp_path / "model.glb").exists()


def test_front_faces_positive_z_in_gltf(worker, tmp_path):
    from glbsmith.glb import read_gltf

    blender = worker()
    build(blender)
    blender.call("finalize")
    gltf = read_gltf(tmp_path / "model.glb")
    node = next(n for n in gltf["nodes"] if n["name"] == "front_marker")
    accessor = gltf["accessors"][gltf["meshes"][node["mesh"]]["primitives"][0]["attributes"]["POSITION"]]
    assert accessor["max"][2] == pytest.approx(0.15, abs=1e-3)  # half the depth, on the +Z side


def test_measure_reports_parts(worker):
    blender = worker()
    build(blender)
    measured = blender.call("measure")
    assert measured["overall_cm"] == {"width_x": 40.0, "depth_y": 30.0, "height_z": 120.0}
    post = next(p for p in measured["parts"] if p["name"] == "post")
    assert post["size_cm"][0] == 5.0


def test_render_writes_images_and_leaves_no_rig_behind(worker, tmp_path):
    blender = worker()
    build(blender)
    result = blender.call("render_views", views=["front", "angled"], label="t", timeout=300)
    assert [Path(v["path"]).exists() for v in result["views"]] == [True, True]
    assert sorted(p["name"] for p in blender.call("measure")["parts"]) == ["base", "front_marker", "post"]


def test_procedural_colour_is_baked_into_the_glb(worker, tmp_path):
    blender = worker(width=20, depth=20, height=20)
    code = """
import bpy
bpy.ops.mesh.primitive_cube_add(size=0.2, location=(0, 0, 0.1))
obj = bpy.context.object
material = bpy.data.materials.new("wood")
material.use_nodes = True
nodes = material.node_tree.nodes
noise = nodes.new("ShaderNodeTexNoise")
material.node_tree.links.new(noise.outputs["Color"], nodes["Principled BSDF"].inputs["Base Color"])
obj.data.materials.append(material)
"""
    assert blender.call("run_python", code=code)["error"] is None
    assert blender.call("finalize")["flat_materials_warning"] == ["wood"]
    result = blender.call("bake_materials")
    assert len(result["baked"]) == 1 and result["still_procedural"] == []
    assert blender.call("finalize")["flat_materials_warning"] == []
    glb = bounds(tmp_path / "model.glb")
    assert glb["textures"] == 1 and glb["materials"] == 1


@pytest.mark.parametrize("code, blocked", [
    ("open('{outside}', 'w').write('x')", "write outside the job"),
    ("open('{secret}').read()", "read files in the home directory"),
    ("import socket; socket.create_connection(('1.1.1.1', 80), 2)", "use the network"),
    ("import subprocess; subprocess.run(['/usr/bin/true'])", "start processes"),
])
def test_sandbox_blocks(worker, tmp_path, code, blocked):
    # Both paths are ones this user can reach, so only the sandbox can refuse them.
    outside = tmp_path.parent / "escaped.txt"
    blender = worker()
    assert blender.info["sandbox"] is True
    result = blender.call("run_python", code=code.format(outside=outside, secret=Path(__file__).resolve()))
    assert result["error"], f"should not be able to {blocked}"
    assert not outside.exists()


def test_sandbox_allows_the_job_folder(worker, tmp_path):
    blender = worker()
    result = blender.call("run_python", code="open('note.txt', 'w').write('ok')")
    assert result["error"] is None
    assert (tmp_path / "note.txt").read_text() == "ok"


def test_item_smaller_than_the_box_keeps_its_proportions(worker, tmp_path):
    # The box is much wider than the item: the height sets the scale, nothing gets stretched.
    blender = worker(width=110, depth=110, height=120)
    build(blender)
    result = blender.call("finalize")
    assert result["ok"]
    assert result["smaller_than_box"] == {"width_x": {"box_cm": 110, "model_cm": 40.0}, "depth_y": {"box_cm": 110, "model_cm": 30.0}}
    assert bounds(tmp_path / "model.glb")["size"] == pytest.approx([0.4, 1.2, 0.3], abs=1e-4)


def test_model_touching_the_box_nowhere_is_refused(worker, tmp_path):
    blender = worker(width=80, depth=60, height=240)
    build(blender)  # half the box on every axis
    result = blender.call("finalize")
    assert not result["ok"] and "does not reach" in result["reason"]
    assert not (tmp_path / "model.glb").exists()


def test_small_overshoot_on_one_axis_does_not_distort_the_others(worker, tmp_path):
    # Height is 1% over a tall box; the narrower width and depth shrink with it instead of snapping.
    blender = worker(width=100, depth=100, height=120)
    build(blender, h=1.2 * 1.01)
    result = blender.call("finalize")
    assert result["ok"]
    size = bounds(tmp_path / "model.glb")["size"]
    assert size[1] == pytest.approx(1.2, abs=1e-4)
    assert size[0] == pytest.approx(0.4 / 1.01, abs=1e-3)
