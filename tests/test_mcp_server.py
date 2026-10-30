"""The MCP server over real stdio with real Blender, as an agent sees it. Uses no agent quota."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image

from glbsmith.blender_client import blender_binary
from glbsmith.glb import bounds

PROJECT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(not Path(blender_binary()).exists(), reason="Blender is not installed")

BUILD = """
import bpy
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.5))
bpy.context.object.name = "body"
bpy.context.object.scale = (0.5, 0.25, 1)
"""


async def scenario(job: Path) -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "glbsmith.mcp_server", str(job)],
                                   env={**os.environ, "PYTHONPATH": str(PROJECT)})
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        names = {tool.name for tool in (await session.list_tools()).tools}
        assert {"get_brief", "run_python", "measure", "render_views", "check_against_photo", "finalize"} <= names

        brief = await session.call_tool("get_brief", {})
        assert [c.type for c in brief.content] == ["text", "text", "image"]

        # Nothing built, never checked: no file.
        refused = await session.call_tool("finalize", {})
        assert json.loads(refused.content[0].text)["ok"] is False

        ran = await session.call_tool("run_python", {"code": BUILD})
        assert json.loads(ran.content[0].text)["error"] is None

        measured = json.loads((await session.call_tool("measure", {})).content[0].text)
        assert measured["overall_cm"]["height_z"] == 100.0

        render = await session.call_tool("render_views", {"views": ["front"]})
        assert [c.type for c in render.content] == ["text", "image"]

        unchecked = json.loads((await session.call_tool("finalize", {})).content[0].text)
        assert not unchecked["ok"] and "check_against_photo" in unchecked["reason"]
        check = await session.call_tool("check_against_photo", {})
        assert [c.type for c in check.content] == ["text", "image"]

        done = json.loads((await session.call_tool("finalize", {})).content[0].text)
        assert done["ok"] and done["glb_size_cm"] == {"width": 50.0, "height": 100.0, "depth": 25.0}


def test_all_tools_over_stdio(tmp_path):
    Image.new("RGB", (8, 8), (255, 255, 255)).save(tmp_path / "photo.png")
    (tmp_path / "brief.json").write_text(json.dumps({"width": 50, "depth": 25, "height": 100, "image": "photo.png"}))
    asyncio.run(asyncio.wait_for(scenario(tmp_path), 180))
    assert bounds(tmp_path / "model.glb")["size"] == pytest.approx([0.5, 1.0, 0.25], abs=1e-4)
    assert (tmp_path / "report.json").exists()
