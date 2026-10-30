"""Job queue for the web app: one job at a time, state kept in each job's folder."""

import json
import os
import queue
import signal
import threading
from pathlib import Path

from . import run as runner


class Jobs:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.queue: queue.Queue = queue.Queue()
        self.process = None
        self.current = None
        self._lock = threading.Lock()
        for path in root.iterdir():  # a restart ends whatever was running
            state = self._read(path)
            if state and state["status"] in ("queued", "running"):
                self._write(path.name, {**state, "status": "failed", "error": "The server restarted during this job."})
        threading.Thread(target=self._loop, daemon=True).start()

    def _read(self, path: Path) -> dict | None:
        try:
            return json.loads((path / "state.json").read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def _write(self, job_id: str, state: dict) -> None:
        path = self.root / job_id / "state.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state))
        temporary.replace(path)

    def path(self, job_id: str) -> Path | None:
        path = self.root / job_id
        return path if job_id.isalnum() and (path / "brief.json").exists() else None

    def get(self, job_id: str) -> dict | None:
        path = self.path(job_id)
        if not path:
            return None
        state = self._read(path) or {"status": "unknown"}
        brief = json.loads((path / "brief.json").read_text())
        report = {}
        with_report = path / "report.json"
        if with_report.exists():
            report = json.loads(with_report.read_text())
        return {"id": job_id, **state, "brief": {k: brief.get(k) for k in ("name", "width", "depth", "height")},
                "has_model": (path / "model.glb").exists() and state.get("status") == "done", "report": report,
                "created": (path / "brief.json").stat().st_mtime}

    def all(self) -> list[dict]:
        found = [self.get(p.name) for p in self.root.iterdir() if p.is_dir()]
        return sorted((j for j in found if j), key=lambda j: -j["created"])

    def submit(self, image, *, width, depth, height, agent, model=None, name=None, allow_unsandboxed=False) -> str:
        job = runner.create_job(image, width, depth, height, name=name, jobs=self.root, allow_unsandboxed=allow_unsandboxed)
        self._write(job.name, {"status": "queued", "agent": agent, "model": model})
        self.queue.put(job.name)
        return job.name

    def cancel(self, job_id: str) -> bool:
        state = self.get(job_id)
        if not state or state["status"] not in ("queued", "running"):
            return False
        self._write(job_id, {"status": "cancelled", "agent": state.get("agent"), "model": state.get("model")})
        with self._lock:
            if self.current == job_id and self.process:
                # The agent, the MCP server and Blender share one process group.
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        return True

    def _loop(self) -> None:
        while True:
            job_id = self.queue.get()
            state = self._read(self.root / job_id)
            if not state or state["status"] != "queued":
                continue  # cancelled while waiting
            with self._lock:
                self.current = job_id
            self._write(job_id, {**state, "status": "running"})

            def attach(process):
                with self._lock:
                    self.process = process

            try:
                summary = runner.run_job(self.root / job_id, state["agent"], model=state.get("model"), on_process=attach)
                final = {"status": "done" if summary["ok"] else "failed", "seconds": summary["seconds"],
                         "message": summary.get("last_text"), "error": summary.get("error")}
                if not summary["ok"] and not final["error"]:
                    final["error"] = "The agent stopped without producing a model that passed the size check."
            except Exception as error:  # noqa: BLE001
                final = {"status": "failed", "error": str(error)}
            with self._lock:
                self.current = self.process = None
            if (self._read(self.root / job_id) or {}).get("status") != "cancelled":
                self._write(job_id, {**state, **final})
