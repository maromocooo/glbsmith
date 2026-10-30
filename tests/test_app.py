"""The web API with the fake agent and real Blender. Uses no agent quota."""

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
