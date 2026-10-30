"""Web app: python -m glbsmith.app (http://127.0.0.1:8021)."""

import asyncio
import io
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

from .agents import AGENTS
from .blender_client import blender_binary
from .jobs import Jobs
from .run import JOBS, brief_images

PROJECT = Path(__file__).resolve().parent.parent
MAX_UPLOAD = 25 * 1024 * 1024
MAX_PHOTOS = 8
MAX_SIZE_CM = 2000
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def landlock_abi() -> int:
    sys.path.insert(0, str(PROJECT / "blender"))
    import sandbox

    return sandbox.landlock_abi()


def create_app(root: Path = JOBS) -> FastAPI:
    app = FastAPI(title="glbsmith")
    jobs = Jobs(root)
    app.state.jobs = jobs

    @app.middleware("http")
    async def same_origin_only(request: Request, call_next):
        # Running an agent costs quota and runs code: other websites and rebinding DNS names must not reach it.
        host = request.headers.get("host", "")
        if host.rsplit(":", 1)[0] not in LOCAL_HOSTS:
            return JSONResponse({"detail": "Local access only."}, 403)
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != host:
            return JSONResponse({"detail": "Cross-origin request refused."}, 403)
        return await call_next(request)

    @app.get("/api/status")
    def status():
        return {
            "agents": [{"id": a.id, "label": a.label, "available": a.available()} for a in AGENTS.values()],
            "blender": Path(blender_binary()).exists(),
            "sandbox": landlock_abi() > 0,
        }

    @app.get("/api/agents/{agent_id}/models")
    def agent_models(agent_id: str):
        if agent_id not in AGENTS:
            raise HTTPException(404, "No such agent.")
        return AGENTS[agent_id].models()

    @app.get("/api/settings")
    def settings():
        return jobs.load()

    @app.put("/api/settings")
    def change_settings(concurrency: int = Body(..., embed=True, ge=1)):
        jobs.set_concurrency(concurrency)
        return jobs.load()

    @app.get("/api/jobs")
    def list_jobs():
        return jobs.all()

    @app.post("/api/jobs", status_code=201)
    async def create_job(images: list[UploadFile] = File(...), masks: list[UploadFile] = File([]), width: float = Form(...), depth: float = Form(...), height: float = Form(...),
                         agent: str = Form(...), model: str | None = Form(None), name: str | None = Form(None),
                         allow_unsandboxed: bool = Form(False)):
        if agent not in AGENTS or not AGENTS[agent].available():
            raise HTTPException(400, f"Agent not available: {agent}")
        if not all(0 < v <= MAX_SIZE_CM for v in (width, depth, height)):
            raise HTTPException(400, f"Sizes must be between 0 and {MAX_SIZE_CM} cm.")
        if not Path(blender_binary()).exists():
            raise HTTPException(400, "Blender was not found. Set BLENDER_BIN.")
        if landlock_abi() < 1 and not allow_unsandboxed:
            raise HTTPException(400, "The sandbox is not available on this system.")
        if model and not re.fullmatch(r"[\w.\-:\[\]]{1,80}", model):
            raise HTTPException(400, "Invalid model name.")
        if len(images) > MAX_PHOTOS:
            raise HTTPException(400, f"At most {MAX_PHOTOS} photos.")
        if masks and len(masks) != len(images):
            raise HTTPException(400, "Send one mask per photo (empty for none).")

        async def read_image(upload: UploadFile) -> io.BytesIO:
            data = await upload.read(MAX_UPLOAD + 1)
            if len(data) > MAX_UPLOAD:
                raise HTTPException(413, "The image is too large.")
            try:
                with Image.open(io.BytesIO(data)) as probe:
                    probe.verify()
            except Exception as error:  # noqa: BLE001 - Pillow raises many types for bad files
                raise HTTPException(400, "That file is not a readable image.") from error
            return io.BytesIO(data)

        pairs = []
        for index, upload in enumerate(images):
            photo = await read_image(upload)
            mask = masks[index] if masks else None
            # An empty part means this photo has no painted area.
            painted = await read_image(mask) if mask is not None and mask.size else None
            pairs.append((photo, painted))
        job_id = await asyncio.to_thread(
            jobs.submit, pairs, width=width, depth=depth, height=height, agent=agent, model=model or None,
            name=(name or "").strip()[:120] or None, allow_unsandboxed=allow_unsandboxed)
        return jobs.get(job_id)

    def job_path(job_id: str) -> Path:
        path = jobs.path(job_id)
        if not path:
            raise HTTPException(404, "No such job.")
        return path

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job_path(job_id)
        return jobs.get(job_id)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        job_path(job_id)
        return {"cancelled": jobs.cancel(job_id)}

    @app.get("/api/jobs/{job_id}/reference/{number}")
    def reference(job_id: str, number: int):
        path = job_path(job_id)
        photos = brief_images(json.loads((path / "brief.json").read_text()))
        if not 0 <= number < len(photos):
            raise HTTPException(404, "No such photo.")
        return FileResponse(path / photos[number]["view"])

    @app.get("/api/jobs/{job_id}/model.glb")
    def model(job_id: str):
        path = job_path(job_id) / "model.glb"
        if not path.exists():
            raise HTTPException(404, "No model yet.")
        return FileResponse(path, media_type="model/gltf-binary", filename="model.glb")

    @app.get("/api/jobs/{job_id}/renders/{name}")
    def render(job_id: str, name: str):
        path = job_path(job_id) / "renders" / name
        if not re.fullmatch(r"\w+\.png", name) or not path.exists():
            raise HTTPException(404, "No such render.")
        return FileResponse(path)

    @app.get("/api/jobs/{job_id}/events")
    async def events(job_id: str, request: Request):
        path = job_path(job_id)

        async def stream():
            offsets = {"agent.jsonl": 0, "events.jsonl": 0}
            sent_state = None
            while not await request.is_disconnected():
                for name in offsets:
                    try:
                        with (path / name).open() as log:
                            log.seek(offsets[name])
                            lines = log.readlines()
                            offsets[name] = log.tell()
                    except OSError:
                        continue
                    for line in lines:
                        yield f"data: {line.strip()}\n\n"
                state = jobs.get(job_id)
                if state["status"] != sent_state:
                    sent_state = state["status"]
                    yield f"event: state\ndata: {json.dumps(state)}\n\n"
                    if sent_state not in ("queued", "running"):
                        return
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    dist = PROJECT / "web" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("GLBSMITH_PORT", "8021")), log_level="info")


if __name__ == "__main__":
    main()
