"""Builds a simple two-part item through the real MCP tools, as an agent would."""

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BUILD = """
import bpy
W, D, H = {width} / 100, {depth} / 100, {height} / 100
def part(name, size, z, colour):
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, z))
    obj = bpy.context.object
    obj.name = name
    obj.scale = size
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (*colour, 1)
    obj.data.materials.append(material)
part("base", (W, D, H * 0.1), H * 0.05, (0.15, 0.15, 0.17))
part("top", (W * 0.5, D * 0.5, H * 0.9), H * 0.55, (0.7, 0.45, 0.25))
"""


def say(**event) -> None:
    print(json.dumps(event), flush=True)


# Makes the upper part 1.5 times as tall, like an instruction to lengthen it.
REVISE = """
import bpy
top = bpy.data.objects["top"]
grow = top.dimensions.z * 0.5
top.scale.z *= 1.5
top.location.z += grow / 2
"""


async def main(job: str) -> None:
    brief = json.load(open(f"{job}/brief.json"))
    pause = float(os.environ.get("GLBSMITH_FAKE_PAUSE", "0"))
    params = StdioServerParameters(command=sys.executable, args=["-m", "glbsmith.mcp_server", job], env=dict(os.environ))
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        async def call(name, **args):
            say(kind="tool", name=name)
            await asyncio.sleep(pause)
            return await session.call_tool(name, args)

        if brief.get("revision"):
            await call("get_revision_requests")
            await call("run_python", code=REVISE)
            await call("render_views", views=["front"], show_marks=True)
            await call("finalize")
            say(kind="text", text="Fake agent: made the upper part taller.")
            return
        await call("get_brief")
        say(kind="text", text="Fake agent: building two boxes.")
        await call("run_python", code=BUILD.format(**brief))
        await call("render_views", views=["front", "angled"])
        await call("measure")
        await call("check_against_photo")
        await call("finalize")
    say(kind="text", text="Fake agent: done. This is a placeholder, not a real model.")


asyncio.run(main(sys.argv[1]))
