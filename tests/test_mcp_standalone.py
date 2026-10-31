"""The server as any AI agent would use it, over real stdio with real Blender. Uses no agent quota."""

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
TALLER = """
import bpy
body = bpy.data.objects["body"]
body.scale.z = 1.5
body.location.z = 0.75
"""


def connect(jobs: Path, **env):
    return StdioServerParameters(command=sys.executable, args=["-m", "glbsmith.mcp_server"],
                                 env={**os.environ, "PYTHONPATH": str(PROJECT), "GLBSMITH_JOBS": str(jobs), **env})


def text_of(result) -> str:
    return "\n".join(c.text for c in result.content if c.type == "text")


def state_of(jobs: Path, model_id: str) -> dict:
    return json.loads((jobs / model_id / "state.json").read_text())


def run(scenario):
    asyncio.run(asyncio.wait_for(scenario, 240))


@pytest.fixture
def photo(tmp_path) -> Path:
    path = tmp_path / "photo.png"
    image = Image.new("RGB", (64, 64), (255, 255, 255))
    image.paste((30, 30, 30), (16, 4, 48, 60))
    image.save(path)
    return path


def test_any_agent_can_make_and_revise_a_model(tmp_path, photo):
    jobs = tmp_path / "jobs"

    async def scenario():
        async with stdio_client(connect(jobs)) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert {"start_model", "revise_model", "list_models", "run_python", "finalize"} <= names

            # Nothing is open yet, and it says what to do.
            early = await session.call_tool("run_python", {"code": BUILD})
            assert early.isError and "start_model" in text_of(early)

            started = await session.call_tool("start_model", {"photos": [str(photo)], "width_cm": 50, "depth_cm": 25, "height_cm": 100, "name": "box"})
            assert not started.isError, text_of(started)
            text = text_of(started)
            assert "get_brief" in text and "check_against_photo" in text, "the steps come with it"
            model_id = text.split()[1]
            assert state_of(jobs, model_id)["status"] == "running"

            brief = await session.call_tool("get_brief", {})
            assert [c.type for c in brief.content] == ["text", "text", "image"]
            await session.call_tool("run_python", {"code": BUILD})
            unchecked = json.loads(text_of(await session.call_tool("finalize", {})))
            assert not unchecked["ok"]
            await session.call_tool("check_against_photo", {"azimuth": 0, "elevation": 0})
            done = json.loads(text_of(await session.call_tool("finalize", {})))
            assert done["ok"] and done["model_id"] == model_id
            assert Path(done["glb_path"]).exists()
            assert state_of(jobs, model_id)["status"] == "done"
            assert state_of(jobs, model_id)["agent"] == "mcp"

            listed = await session.call_tool("list_models", {})
            assert model_id in text_of(listed) and done["glb_path"] in text_of(listed)

            # A change makes a new model, and leaves the first alone.
            before = (jobs / model_id / "model.glb").read_bytes()
            revising = await session.call_tool("revise_model", {"model_id": model_id, "instruction": "make it taller"})
            assert not revising.isError, text_of(revising)
            new_id = text_of(revising).split()[1]
            assert new_id != model_id
            requests = await session.call_tool("get_revision_requests", {})
            assert "make it taller" in text_of(requests)
            await session.call_tool("run_python", {"code": TALLER})
            revised = json.loads(text_of(await session.call_tool("finalize", {})))
            assert revised["ok"] and revised["glb_size_cm"]["height"] == 150.0, "a revision may leave the box"
            assert (jobs / model_id / "model.glb").read_bytes() == before
            assert state_of(jobs, new_id)["revision_of"] == model_id
            assert state_of(jobs, new_id)["revision_number"] == 1
            again = await session.call_tool("revise_model", {"model_id": new_id, "instruction": "again"})
            assert state_of(jobs, text_of(again).split()[1])["revision_number"] == 2

    run(scenario())
    assert bounds(jobs / sorted(jobs.iterdir(), key=lambda p: p.stat().st_mtime)[0].name / "model.glb")["size"] == pytest.approx([0.5, 1.0, 0.25], abs=1e-3)


def test_bad_requests_say_what_is_wrong(tmp_path, photo):
    jobs = tmp_path / "jobs"
    not_image = tmp_path / "notes.txt"
    not_image.write_text("hello")

    async def scenario():
        async with stdio_client(connect(jobs)) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            good = {"photos": [str(photo)], "width_cm": 50, "depth_cm": 25, "height_cm": 100}
            for change, says in [
                ({"photos": []}, "between 1"),
                ({"photos": [str(photo)] * 9}, "between 1"),
                ({"photos": ["/no/such/file.png"]}, "No such file"),
                ({"photos": [str(not_image)]}, "readable image"),
                ({"width_cm": 0}, "Sizes"),
                ({"height_cm": 99999}, "Sizes"),
                ({"masks": [str(photo), str(photo)]}, "one mask per photo"),
            ]:
                result = await session.call_tool("start_model", {**good, **change})
                assert result.isError and says in text_of(result), change
            for arguments, says in [
                ({"model_id": "nothere", "instruction": "x"}, "No finished model"),
                ({"model_id": "../../etc", "instruction": "x"}, "No finished model"),
            ]:
                result = await session.call_tool("revise_model", arguments)
                assert result.isError and says in text_of(result)

    run(scenario())
    assert not jobs.exists() or not list(jobs.iterdir()), "nothing is left behind by a refused request"


def test_a_model_left_unfinished_is_marked_so_the_web_page_does_not_wait_forever(tmp_path, photo):
    jobs = tmp_path / "jobs"
    made = {}

    async def scenario():
        async with stdio_client(connect(jobs)) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            first = await session.call_tool("start_model", {"photos": [str(photo)], "width_cm": 50, "depth_cm": 25, "height_cm": 100})
            made["first"] = text_of(first).split()[1]
            second = await session.call_tool("start_model", {"photos": [str(photo)], "width_cm": 50, "depth_cm": 25, "height_cm": 100})
            made["second"] = text_of(second).split()[1]
            assert state_of(jobs, made["first"])["status"] == "failed", "starting another leaves the first"
            assert state_of(jobs, made["second"])["status"] == "running"

    run(scenario())
    assert state_of(jobs, made["second"])["status"] == "failed", "the agent went away"


def test_painted_masks_are_used(tmp_path, photo):
    jobs = tmp_path / "jobs"
    mask = tmp_path / "mask.png"
    Image.new("L", (64, 64), 255).save(mask)

    async def scenario():
        async with stdio_client(connect(jobs)) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            started = await session.call_tool("start_model", {"photos": [str(photo), str(photo)], "masks": [str(mask), None], "width_cm": 50, "depth_cm": 25, "height_cm": 100})
            assert not started.isError, text_of(started)
            brief = await session.call_tool("get_brief", {})
            assert "painted the item" in text_of(brief)

    run(scenario())


def test_the_web_app_lists_these_models(tmp_path, photo):
    from fastapi.testclient import TestClient

    from glbsmith.app import create_app

    jobs = tmp_path / "jobs"
    made = {}

    async def scenario():
        async with stdio_client(connect(jobs)) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            started = await session.call_tool("start_model", {"photos": [str(photo)], "width_cm": 50, "depth_cm": 25, "height_cm": 100, "name": "from an agent"})
            made["id"] = text_of(started).split()[1]
            await session.call_tool("run_python", {"code": BUILD})
            await session.call_tool("check_against_photo", {"azimuth": 0, "elevation": 0})
            await session.call_tool("finalize", {})

    run(scenario())
    with TestClient(create_app(jobs), headers={"host": "127.0.0.1:8021"}) as client:
        job = client.get(f"/api/jobs/{made['id']}").json()
        assert job["status"] == "done" and job["has_model"] and job["agent"] == "mcp"
        assert job["brief"]["name"] == "from an agent"
        assert client.get(f"/api/jobs/{made['id']}/model.glb").content[:4] == b"glTF"
