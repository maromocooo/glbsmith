"""The web API with the fake agent and real Blender. Uses no agent quota."""

import io
import os
import subprocess
import time

import pytest

os.environ["GLBSMITH_FAKE_AGENT"] = "1"

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from glbsmith.app import create_app  # noqa: E402
from glbsmith.glb import bounds  # noqa: E402

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


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path), headers=HOST) as client:
        yield client


def submit(client, agent="fake", **form):
    return client.post("/api/jobs", files={"image": ("p.png", photo())},
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
    response = client.post("/api/jobs", files={"image": ("p.jpg", photo(exif=True))},
                           data={"width": 30, "depth": 20, "height": 90, "agent": "fake"})
    job_id = response.json()["id"]
    assert b"SECRET-CAMERA" not in (tmp_path / job_id / "reference.png").read_bytes()
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


def test_not_an_image_is_refused(client):
    response = client.post("/api/jobs", files={"image": ("p.png", b"hello")},
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
