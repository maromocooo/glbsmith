"""Read a GLB's real size back without Blender (standard library only)."""

import json
import struct
from pathlib import Path


def _multiply(a: list[float], b: list[float]) -> list[float]:
    """Column-major 4×4 product a·b, as glTF stores matrices."""
    return [sum(a[row + k * 4] * b[col * 4 + k] for k in range(4)) for col in range(4) for row in range(4)]


def _node_matrix(node: dict) -> list[float]:
    if "matrix" in node:
        return node["matrix"]
    tx, ty, tz = node.get("translation", (0, 0, 0))
    x, y, z, w = node.get("rotation", (0, 0, 0, 1))
    sx, sy, sz = node.get("scale", (1, 1, 1))
    return [
        (1 - 2 * (y * y + z * z)) * sx, 2 * (x * y + z * w) * sx, 2 * (x * z - y * w) * sx, 0,
        2 * (x * y - z * w) * sy, (1 - 2 * (x * x + z * z)) * sy, 2 * (y * z + x * w) * sy, 0,
        2 * (x * z + y * w) * sz, 2 * (y * z - x * w) * sz, (1 - 2 * (x * x + y * y)) * sz, 0,
        tx, ty, tz, 1,
    ]


def read_gltf(path: Path) -> dict:
    data = Path(path).read_bytes()
    if len(data) < 20 or data[:4] != b"glTF":
        raise ValueError("Not a binary glTF file.")
    length, kind = struct.unpack_from("<II", data, 12)
    if kind != 0x4E4F534A:
        raise ValueError("First GLB chunk is not JSON.")
    return json.loads(data[20:20 + length])


def bounds(path: Path) -> dict:
    """Bounding box in metres, from the accessor min/max the glTF spec requires."""
    gltf = read_gltf(path)
    low, high = [float("inf")] * 3, [float("-inf")] * 3

    def visit(index: int, parent: list[float]) -> None:
        node = gltf["nodes"][index]
        world = _multiply(parent, _node_matrix(node))
        if "mesh" in node:
            for primitive in gltf["meshes"][node["mesh"]]["primitives"]:
                accessor = gltf["accessors"][primitive["attributes"]["POSITION"]]
                for ix in (0, 1):
                    for iy in (0, 1):
                        for iz in (0, 1):
                            p = (accessor["min" if ix == 0 else "max"][0],
                                 accessor["min" if iy == 0 else "max"][1],
                                 accessor["min" if iz == 0 else "max"][2])
                            for axis in range(3):
                                v = sum(world[axis + k * 4] * p[k] for k in range(3)) + world[axis + 12]
                                low[axis] = min(low[axis], v)
                                high[axis] = max(high[axis], v)
        for child in node.get("children", ()):
            visit(child, world)

    identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    for root in gltf["scenes"][gltf.get("scene", 0)]["nodes"]:
        visit(root, identity)
    return {"min": low, "max": high, "size": [high[i] - low[i] for i in range(3)],
            "materials": len(gltf.get("materials", ())), "textures": len(gltf.get("textures", ())),
            "meshes": len(gltf.get("meshes", ()))}
