"""The web API with the fake agent and real Blender. Uses no agent quota."""

import base64
import io
import json
import os
import subprocess
import time

import pytest

os.environ["GLBSMITH_FAKE_AGENT"] = "1"

from fastapi.testclient import TestClient  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from glbsmith.app import create_app  # noqa: E402
from glbsmith.glb import bounds  # noqa: E402
from glbsmith.run import token_usage  # noqa: E402

HOST = {"host": "127.0.0.1:8021"}


def photo(exif=False) -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGB", (32, 32), (200, 100, 50))
    if exif:
        data = Image.Exif()
        data[0x010F] = "SECRET-CAMERA"
        image.save(buffer, "JPEG", exif=data)
    else:
        image.save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def private_keys(tmp_path, monkeypatch):
    """Tests never see the person's own keys, saved or exported."""
    monkeypatch.setenv("GLBSMITH_CONFIG", str(tmp_path / "config"))
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path), headers=HOST) as client:
        yield client


def submit(client, agent="fake", **form):
    return client.post("/api/jobs", files={"images": ("p.png", photo())},
                       data={"width": 30, "depth": 20, "height": 90, "agent": agent, **form})


def wait(client, job_id, statuses, seconds=120):
    deadline = time.time() + seconds
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in statuses:
            return job
        time.sleep(0.3)
    raise AssertionError(f"still {job['status']}")


def test_job_runs_to_a_true_size_model(client, tmp_path):
    response = submit(client, name="test item")
    assert response.status_code == 201
    job = wait(client, response.json()["id"], {"done", "failed"})
    assert job["status"] == "done", job
    assert job["has_model"] and job["report"]["ok"]
    assert job["report"]["glb_size_cm"] == {"width": 30.0, "height": 90.0, "depth": 20.0}
    glb = client.get(f"/api/jobs/{job['id']}/model.glb")
    assert glb.content[:4] == b"glTF"
    assert bounds(tmp_path / job["id"] / "model.glb")["size"] == pytest.approx([0.3, 0.9, 0.2], abs=1e-4)
    assert client.get(f"/api/jobs/{job['id']}/renders/001_sheet.png").status_code == 200
    assert client.get("/api/jobs").json()[0]["id"] == job["id"]
    with client.stream("GET", f"/api/jobs/{job['id']}/events") as stream:
        text = "".join(stream.iter_text())
    assert '"kind": "tool"' in text and "event: state" in text and '"kind": "render"' in text


def test_cancel_stops_the_agent_and_blender(client, tmp_path, monkeypatch):
    monkeypatch.setenv("GLBSMITH_FAKE_PAUSE", "20")
    job_id = submit(client).json()["id"]
    wait(client, job_id, {"running"})
    deadline = time.time() + 30
    job_dir = tmp_path / job_id
    while time.time() < deadline and not (job_dir / "agent.jsonl").exists():
        time.sleep(0.2)
    assert client.post(f"/api/jobs/{job_id}/cancel").json()["cancelled"]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "cancelled"
    time.sleep(1.5)
    running = subprocess.run(["pgrep", "-f", str(job_dir)], capture_output=True, text=True).stdout.split()
    assert not running, "processes left behind"
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "cancelled"


def test_exif_is_removed(client, tmp_path):
    response = client.post("/api/jobs", files={"images": ("p.jpg", photo(exif=True))},
                           data={"width": 30, "depth": 20, "height": 90, "agent": "fake"})
    job_id = response.json()["id"]
    assert b"SECRET-CAMERA" not in (tmp_path / job_id / "reference_0.png").read_bytes()
    wait(client, job_id, {"done", "failed"})


@pytest.mark.parametrize("change", [{"agent": "nope"}, {"width": 0}, {"height": -1}, {"depth": 99999}, {"model": "x; rm -rf /"}])
def test_bad_input_is_refused(client, change):
    assert submit(client, **change).status_code == 400


def test_models_come_from_each_cli(client, monkeypatch, tmp_path):
    import json
    import subprocess as sp

    from glbsmith import agents

    catalog = {"models": [
        {"slug": "b-model", "display_name": "B", "visibility": "list", "priority": 2},
        {"slug": "hidden", "display_name": "H", "visibility": "hide", "priority": 0},
        {"slug": "a-model", "display_name": "A", "visibility": "list", "priority": 1},
    ]}
    monkeypatch.setattr(sp, "run", lambda *a, **k: sp.CompletedProcess(a, 0, json.dumps(catalog), ""))
    codex = client.get("/api/agents/codex/models").json()
    assert [m["id"] for m in codex["options"]] == ["a-model", "b-model"]

    def broken(*a, **k):
        raise OSError("not there")

    monkeypatch.setattr(sp, "run", broken)
    assert client.get("/api/agents/codex/models").json()["options"] == []

    (tmp_path / ".claude.json").write_text(json.dumps({"additionalModelOptionsCache": [{"value": "extra-1", "label": "Extra"}, "junk"]}))
    monkeypatch.setattr(agents.Path, "home", lambda: tmp_path)
    claude = client.get("/api/agents/claude/models").json()
    assert [m["id"] for m in claude["options"]] == ["fable", "opus", "sonnet", "extra-1"]
    assert client.get("/api/agents/nope/models").status_code == 404


def test_several_photos_with_a_painted_one(client, tmp_path):
    mask = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    ImageDraw.Draw(mask).rectangle((8, 8, 24, 24), fill=(255, 0, 0, 255))
    painted = io.BytesIO()
    mask.save(painted, "PNG")
    files = [("images", ("a.png", photo())), ("images", ("b.png", photo())),
             ("masks", ("none", b"")), ("masks", ("m.png", painted.getvalue()))]
    response = client.post("/api/jobs", files=files, data={"width": 30, "depth": 20, "height": 90, "agent": "fake"})
    assert response.status_code == 201, response.text
    job = wait(client, response.json()["id"], {"done", "failed"})
    assert job["status"] == "done", job
    assert job["photos"] == [{"painted": False}, {"painted": True}]
    folder = tmp_path / job["id"]
    assert not (folder / "reference_0_mask.png").exists()
    stored = np.asarray(Image.open(folder / "reference_1_mask.png"))
    assert stored[16, 16] == 255 and stored[2, 2] == 0
    # What the agent and the page see is the highlighted version.
    view = np.asarray(Image.open(folder / "reference_1_view.png").convert("RGB"), dtype=int)
    assert view[16, 16].tolist() == [200, 100, 50]
    assert view[2, 2].min() > 190
    assert client.get(f"/api/jobs/{job['id']}/reference/1").content == (folder / "reference_1_view.png").read_bytes()
    assert client.get(f"/api/jobs/{job['id']}/reference/2").status_code == 404


def test_blank_mask_counts_as_none(client, tmp_path):
    blank = io.BytesIO()
    Image.new("RGBA", (32, 32), (0, 0, 0, 0)).save(blank, "PNG")
    response = client.post("/api/jobs", files=[("images", ("a.png", photo())), ("masks", ("m.png", blank.getvalue()))],
                           data={"width": 30, "depth": 20, "height": 90, "agent": "fake"})
    assert response.json()["photos"] == [{"painted": False}]
    wait(client, response.json()["id"], {"done", "failed"})


def test_too_many_photos_or_mismatched_masks_are_refused(client):
    data = {"width": 30, "depth": 20, "height": 90, "agent": "fake"}
    assert client.post("/api/jobs", files=[("images", ("a.png", photo()))] * 9, data=data).status_code == 400
    two = [("images", ("a.png", photo()))] * 2 + [("masks", ("m", b""))]
    assert client.post("/api/jobs", files=two, data=data).status_code == 400


def test_concurrency_runs_jobs_side_by_side_and_can_change_while_running(client, monkeypatch):
    monkeypatch.setenv("GLBSMITH_FAKE_PAUSE", "1.5")
    assert client.get("/api/settings").json()["concurrency"] == 1
    assert client.put("/api/settings", json={"concurrency": 2}).json()["concurrency"] == 2
    ids = [submit(client).json()["id"] for _ in range(3)]
    statuses = lambda: [client.get(f"/api/jobs/{i}").json()["status"] for i in ids]  # noqa: E731
    deadline = time.time() + 20
    while time.time() < deadline and statuses().count("running") < 2:
        time.sleep(0.1)
    assert statuses() == ["running", "running", "queued"]
    assert client.get("/api/settings").json() | {"max": 0} == {"concurrency": 2, "max": 0, "running": 2, "queued": 1}
    # Raising it starts the waiting job straight away, without waiting for a slot.
    if client.get("/api/settings").json()["max"] >= 3:
        client.put("/api/settings", json={"concurrency": 3})
        deadline = time.time() + 10
        while time.time() < deadline and statuses().count("running") < 3:
            time.sleep(0.1)
        assert statuses().count("running") == 3
    for job_id in ids:
        assert wait(client, job_id, {"done", "failed"})["status"] == "done"
    assert client.get("/api/settings").json() | {"max": 0, "concurrency": 0} == {"max": 0, "concurrency": 0, "running": 0, "queued": 0}


def test_concurrency_is_kept_and_limited(client, tmp_path):
    limit = client.get("/api/settings").json()["max"]
    assert client.put("/api/settings", json={"concurrency": 999}).json()["concurrency"] == limit
    assert client.put("/api/settings", json={"concurrency": 0}).status_code == 422
    client.put("/api/settings", json={"concurrency": 1})
    assert json.loads((tmp_path / "settings.json").read_text())["concurrency"] == 1


def test_cancel_a_queued_job_never_starts_it(client, monkeypatch):
    monkeypatch.setenv("GLBSMITH_FAKE_PAUSE", "1")
    first = submit(client).json()["id"]
    second = submit(client).json()["id"]
    assert client.post(f"/api/jobs/{second}/cancel").json()["cancelled"]
    assert wait(client, first, {"done", "failed"})["status"] == "done"
    assert client.get(f"/api/jobs/{second}").json()["status"] == "cancelled"


def request_for(point, color="#ff0000", prompt="make this taller"):
    return {"color": color, "prompt": prompt, "points": [[*point, 0.02]]}


def finished_job(client):
    return wait(client, submit(client).json()["id"], {"done", "failed"})


def test_revision_makes_a_new_job_and_leaves_the_original_alone(client, tmp_path):
    parent = finished_job(client)
    original = (tmp_path / parent["id"] / "model.glb").read_bytes()
    view = base64.b64encode(photo()).decode()
    response = client.post(f"/api/jobs/{parent['id']}/revise", json={
        "requests": [request_for((0, 0.8, 0))], "general": "keep the base", "agent": "fake", "view": f"data:image/png;base64,{view}",
    })
    assert response.status_code == 201, response.text
    assert response.json()["revision_of"] == parent["id"] and response.json()["revision_number"] == 1
    child = wait(client, response.json()["id"], {"done", "failed"})
    assert child["status"] == "done", child
    # Grew past the original 90 cm box, which a revision may do.
    assert child["report"]["glb_size_cm"]["height"] == pytest.approx(130.5, abs=0.5)
    assert child["report"]["photo_check"] is None
    assert (tmp_path / parent["id"] / "model.glb").read_bytes() == original
    assert (tmp_path / child["id"] / "revision_view.png").exists()
    # The revision itself can be revised.
    second = client.post(f"/api/jobs/{child['id']}/revise", json={"general": "again", "agent": "fake"}).json()
    assert second["revision_number"] == 2
    assert wait(client, second["id"], {"done", "failed"})["status"] == "done"


@pytest.mark.parametrize("change", [
    {"requests": [], "general": "  "},
    {"agent": "nope"},
    {"model": "x; rm -rf /"},
    {"view": "data:text/html;base64,AAAA"},
    {"view": "data:image/png;base64,AAAA"},
    {"requests": [request_for((0, 0, 0), color="red")]},
    {"requests": [request_for((0, 0, 0), prompt="")]},
    {"requests": [{"color": "#ff0000", "prompt": "x", "points": []}]},
    {"requests": [request_for((0, 0, 0))] * 6},
    {"requests": [{"color": "#ff0000", "prompt": "x", "points": [[0, 0, 0, 0.01]] * 4001}]},
])
def test_bad_revisions_are_refused(client, change):
    parent = finished_job(client)
    body = {"requests": [request_for((0, 0, 0))], "agent": "fake", **change}
    assert client.post(f"/api/jobs/{parent['id']}/revise", json=body).status_code in (400, 422)


def test_only_a_finished_model_can_be_revised(client, monkeypatch):
    monkeypatch.setenv("GLBSMITH_FAKE_PAUSE", "1")
    running = submit(client).json()["id"]
    assert client.post(f"/api/jobs/{running}/revise", json={"general": "x", "agent": "fake"}).status_code == 400
    assert client.post("/api/jobs/nothere/revise", json={"general": "x", "agent": "fake"}).status_code == 404
    wait(client, running, {"done", "failed"})


def test_token_usage_means_the_same_for_every_agent():
    claude = {"usage": {"usage": {"input_tokens": 12, "cache_creation_input_tokens": 34275, "cache_read_input_tokens": 114601, "output_tokens": 25024}}}
    assert token_usage(claude) == {"total": 173912, "input": 148888, "cached": 114601, "output": 25024}
    codex = {"usage": {"usage": {"input_tokens": 381747, "cached_input_tokens": 349184, "output_tokens": 2495}}}
    assert token_usage(codex) == {"total": 384242, "input": 381747, "cached": 349184, "output": 2495}
    assert token_usage({"usage": None}) is None
    assert token_usage({}) is None


def test_finished_job_reports_tokens(client):
    job = finished_job(client)
    assert job["tokens"] == {"total": 12345, "input": 12000, "cached": 9000, "output": 345}
    assert client.get("/api/jobs").json()[0]["tokens"]["total"] == 12345


def test_cancelled_job_has_no_tokens_and_does_not_break(client, monkeypatch):
    monkeypatch.setenv("GLBSMITH_FAKE_PAUSE", "5")
    job_id = submit(client).json()["id"]
    wait(client, job_id, {"running"})
    client.post(f"/api/jobs/{job_id}/cancel")
    deadline = time.time() + 15
    while time.time() < deadline and client.get("/api/settings").json()["running"]:
        time.sleep(0.2)
    assert client.get(f"/api/jobs/{job_id}").json()["tokens"] is None
    assert client.get("/api/jobs").status_code == 200


def data_url(content: bytes, kind="png") -> str:
    return f"data:image/{kind};base64,{base64.b64encode(content).decode()}"


def test_images_attached_to_instructions_reach_the_agent_cleaned(client, tmp_path):
    parent = finished_job(client)
    secret = photo(exif=True)
    body = {
        "requests": [{**request_for((0, 0.8, 0)), "images": [data_url(secret, "jpeg"), data_url(photo())]}],
        "general": "like this one", "general_images": [data_url(photo())], "agent": "fake",
    }
    response = client.post(f"/api/jobs/{parent['id']}/revise", json=body)
    assert response.status_code == 201, response.text
    child = wait(client, response.json()["id"], {"done", "failed"})
    assert child["status"] == "done", child
    folder = tmp_path / child["id"]
    saved = json.loads((folder / "revision.json").read_text())
    assert saved["requests"][0]["images"] == ["attached_colour1_1.png", "attached_colour1_2.png"]
    assert saved["general_images"] == ["attached_general_1.png"]
    assert b"SECRET-CAMERA" not in (folder / "attached_colour1_1.png").read_bytes()
    assert "3 attached image(s)" in (folder / "agent.jsonl").read_text()
    # A revision of it starts clean: attachments belong to the instruction they came with.
    second = client.post(f"/api/jobs/{child['id']}/revise", json={"general": "more", "agent": "fake"}).json()
    assert wait(client, second["id"], {"done", "failed"})["status"] == "done"
    assert not list((tmp_path / second["id"]).glob("attached_*"))
    assert "0 attached image(s)" in (tmp_path / second["id"] / "agent.jsonl").read_text()


@pytest.mark.parametrize("images", [
    ["data:text/html;base64,AAAA"], ["data:image/png;base64,AAAA"], ["not a url"],
    [data_url(photo())] * 5,
])
def test_bad_attachments_are_refused(client, images):
    parent = finished_job(client)
    body = {"requests": [{**request_for((0, 0, 0)), "images": images}], "agent": "fake"}
    assert client.post(f"/api/jobs/{parent['id']}/revise", json=body).status_code in (400, 422)
    assert client.post(f"/api/jobs/{parent['id']}/revise", json={"general": "x", "general_images": images, "agent": "fake"}).status_code in (400, 422)


KEY = "sk-test-0123456789-secret"


def agent_ids(client):
    return [a["id"] for a in client.get("/api/status").json()["agents"]]


def test_api_agents_appear_with_a_key_and_leave_with_it(client, tmp_path):
    assert "claude-api" not in agent_ids(client) and "codex-api" not in agent_ids(client)
    described = client.put("/api/keys/anthropic", json={"key": KEY}).json()
    assert described["anthropic"] == {"set": True, "source": "saved", "ends_with": "cret", "environment": "ANTHROPIC_API_KEY"}
    assert described["openai"]["set"] is False
    ids = agent_ids(client)
    if "claude" in ids:
        assert "claude-api" in ids and "codex-api" not in ids
        agent = next(a for a in client.get("/api/status").json()["agents"] if a["id"] == "claude-api")
        assert agent["label"] == "Claude Code (API)" and agent["billing"] == "api"
    client.put("/api/keys/anthropic", json={"key": ""})
    assert client.get("/api/keys").json()["anthropic"]["set"] is False
    assert "claude-api" not in agent_ids(client)


def test_the_key_never_comes_back_and_is_stored_privately(client, tmp_path):
    client.put("/api/keys/openai", json={"key": KEY})
    assert KEY not in client.get("/api/keys").text and KEY not in client.get("/api/status").text
    path = tmp_path / "config" / "keys.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert KEY in path.read_text()


def test_a_key_in_the_environment_counts_but_is_not_saved(client, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    assert client.get("/api/keys").json()["openai"]["source"] == "environment"
    assert not (tmp_path / "config" / "keys.json").exists()


def test_only_the_api_agents_get_the_key(client, tmp_path, monkeypatch):
    from glbsmith.agents import AGENTS

    monkeypatch.setenv("ANTHROPIC_API_KEY", "exported-anthropic-key")
    monkeypatch.setenv("OPENAI_API_KEY", "exported-openai-key")
    job = tmp_path / "job"
    job.mkdir()
    (job / "brief.json").write_text("{}")
    for agent in ("claude", "codex"):
        env = AGENTS[agent].launch(job, None).env
        assert not {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CODEX_API_KEY"} & set(env), agent
    assert AGENTS["claude-api"].launch(job, None).env["ANTHROPIC_API_KEY"] == "exported-anthropic-key"
    assert AGENTS["codex-api"].launch(job, None).env["CODEX_API_KEY"] == "exported-openai-key"
    # It goes through the environment only, never into a file or the command line.
    launch = AGENTS["claude-api"].launch(job, None)
    assert "exported-anthropic-key" not in " ".join(launch.args)
    assert "exported-anthropic-key" not in (job / "mcp.json").read_text()


def test_codex_errors_read_as_text():
    from glbsmith.agents import AGENTS

    line = json.dumps({"type": "turn.failed", "error": {"message": "401 Unauthorized"}})
    assert AGENTS["codex"].parse(line) == [{"kind": "error", "text": "401 Unauthorized"}]
    assert AGENTS["codex"].parse(json.dumps({"type": "error", "message": "boom"})) == [{"kind": "error", "text": "boom"}]


def test_blender_never_sees_a_key(tmp_path, monkeypatch):
    from glbsmith.blender_client import BlenderWorker

    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    Image.new("RGB", (8, 8)).save(tmp_path / "p.png")
    (tmp_path / "brief.json").write_text(json.dumps({"width": 10, "depth": 10, "height": 10, "image": "p.png"}))
    worker = BlenderWorker(tmp_path)
    worker.start()
    try:
        output = worker.call("run_python", code="import os; print(sorted(os.environ))")["output"]
    finally:
        worker.stop()
    assert "ANTHROPIC_API_KEY" not in output and "HOME" in output


def test_a_job_whose_key_is_gone_fails_clearly(client, monkeypatch):
    import glbsmith.agents as agents

    monkeypatch.setitem(agents.AGENTS, "claude-api", agents.ClaudeApiAgent())
    monkeypatch.setattr(agents.shutil, "which", lambda name: "/usr/bin/" + name)
    client.put("/api/keys/anthropic", json={"key": KEY})
    job_id = submit(client, agent="claude-api").json()["id"]
    client.put("/api/keys/anthropic", json={"key": ""})
    job = wait(client, job_id, {"done", "failed"})
    assert job["status"] == "failed"


@pytest.mark.parametrize("provider, key, status", [("nope", KEY, 404), ("openai", "short", 400), ("openai", "has spaces in it", 400), ("openai", "x" * 501, 422)])
def test_bad_keys_are_refused(client, provider, key, status):
    assert client.put(f"/api/keys/{provider}", json={"key": key}).status_code == status


def test_other_sites_cannot_set_a_key(client):
    response = client.put("/api/keys/openai", json={"key": KEY}, headers={"origin": "https://evil.example"})
    assert response.status_code == 403
    assert client.get("/api/keys").json()["openai"]["set"] is False


def test_not_an_image_is_refused(client):
    response = client.post("/api/jobs", files={"images": ("p.png", b"hello")},
                           data={"width": 30, "depth": 20, "height": 90, "agent": "fake"})
    assert response.status_code == 400


@pytest.mark.parametrize("headers", [{"origin": "https://evil.example"}, {"host": "evil.example"}])
def test_other_sites_are_refused(client, headers):
    assert client.get("/api/jobs", headers=headers).status_code == 403


def test_path_traversal_is_refused(client):
    assert client.get("/api/jobs/..%2F..%2Fetc").status_code == 404
    job_id = submit(client).json()["id"]
    assert client.get(f"/api/jobs/{job_id}/renders/..%2Fbrief.json").status_code == 404
    wait(client, job_id, {"done", "failed"})
