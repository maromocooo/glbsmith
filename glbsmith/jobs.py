"""Job queue for the web app: runs up to `concurrency` jobs at once, state kept in each job's folder."""

import json
import os
import shutil
import signal
import threading
import uuid
from collections import deque
from pathlib import Path

from . import run as runner

MAX_CONCURRENCY = min(8, os.cpu_count() or 1)


class Jobs:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.queue: deque[str] = deque()
        self.running: dict[str, object] = {}  # job id -> agent process, or None while it starts
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self.concurrency = self._saved_concurrency()
        for path in root.iterdir():  # a restart ends whatever was running
            state = self._read(path)
            if state and state["status"] in ("queued", "running"):
                self._write(path.name, {**state, "status": "failed", "error": "The server restarted during this job."})
        threading.Thread(target=self._dispatch, daemon=True).start()

    # ---- settings ----
    def _saved_concurrency(self) -> int:
        try:
            value = json.loads((self.root / "settings.json").read_text())["concurrency"]
            return max(1, min(int(value), MAX_CONCURRENCY))
        except (OSError, ValueError, KeyError, TypeError):
            return 1

    def set_concurrency(self, value: int) -> None:
        """Takes effect at once: raising it starts waiting jobs; lowering it lets running ones finish."""
        with self._changed:
            self.concurrency = max(1, min(value, MAX_CONCURRENCY))
            (self.root / "settings.json").write_text(json.dumps({"concurrency": self.concurrency}))
            self._changed.notify_all()

    def load(self) -> dict:
        with self._lock:
            return {"concurrency": self.concurrency, "max": MAX_CONCURRENCY, "running": len(self.running), "queued": len(self.queue)}

    # ---- files ----
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
                "photos": [{"painted": bool(p["mask"])} for p in runner.brief_images(brief)],
                "has_model": (path / "model.glb").exists() and state.get("status") == "done", "report": report,
                "created": (path / "brief.json").stat().st_mtime}

    def all(self) -> list[dict]:
        found = [self.get(p.name) for p in self.root.iterdir() if p.is_dir()]
        return sorted((j for j in found if j), key=lambda j: -j["created"])

    # ---- queue ----
    def submit(self, images, *, width, depth, height, agent, model=None, name=None, allow_unsandboxed=False) -> str:
        job = runner.create_job(images, width, depth, height, name=name, jobs=self.root, allow_unsandboxed=allow_unsandboxed)
        with self._changed:
            self._write(job.name, {"status": "queued", "agent": agent, "model": model})
            self.queue.append(job.name)
            self._changed.notify_all()
        return job.name

    def revise(self, parent_id: str, *, requests: list[dict], general: str, agent: str, model: str | None, view: bytes | None) -> str:
        """A new job that continues from a finished model. The original stays as it was."""
        parent = self.root / parent_id
        report = json.loads((parent / "report.json").read_text())
        state = self._read(parent) or {}
        job = self.root / uuid.uuid4().hex[:12]
        job.mkdir()
        brief = json.loads((parent / "brief.json").read_text())
        brief.pop("render_threads", None)
        photos = runner.brief_images(brief)
        for photo in photos:
            for key in ("file", "mask", "view"):
                if photo[key] and not (job / photo[key]).exists():
                    shutil.copyfile(parent / photo[key], job / photo[key])
        shutil.copyfile(parent / "model.blend", job / "model.blend")
        (job / "revision.json").write_text(json.dumps({
            "parent": parent_id, "requests": requests, "general": general, "glb_size_cm": report["glb_size_cm"],
        }))
        if view:
            (job / "revision_view.png").write_bytes(view)
        (job / "brief.json").write_text(json.dumps({**brief, "images": photos, "revision": True}))
        with self._changed:
            self._write(job.name, {"status": "queued", "agent": agent, "model": model, "revision_of": parent_id,
                                   "revision_number": state.get("revision_number", 0) + 1})
            self.queue.append(job.name)
            self._changed.notify_all()
        return job.name

    def can_revise(self, job_id: str) -> bool:
        job = self.get(job_id)
        path = self.root / job_id
        return bool(job and job["has_model"] and (path / "model.blend").exists() and (path / "report.json").exists())

    def cancel(self, job_id: str) -> bool:
        with self._changed:
            state = self._read(self.root / job_id)
            if not state or state["status"] not in ("queued", "running"):
                return False
            self._write(job_id, {**state, "status": "cancelled"})
            if job_id in self.queue:
                self.queue.remove(job_id)
            self._kill(self.running.get(job_id))
            self._changed.notify_all()
        return True

    def _kill(self, process) -> None:
        if process:
            # The agent, the MCP server and Blender share one process group.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def _dispatch(self) -> None:
        while True:
            with self._changed:
                while not self.queue or len(self.running) >= self.concurrency:
                    self._changed.wait()
                job_id = self.queue.popleft()
                self.running[job_id] = None
                threads = max(1, (os.cpu_count() or 1) // self.concurrency)
            threading.Thread(target=self._run, args=(job_id, threads), daemon=True).start()

    def _run(self, job_id: str, threads: int) -> None:
        path = self.root / job_id
        state = self._read(path) or {}
        final = None
        try:
            brief = json.loads((path / "brief.json").read_text())
            temporary = path / "brief.tmp"  # the page reads this file at any moment
            temporary.write_text(json.dumps({**brief, "render_threads": threads}))
            temporary.replace(path / "brief.json")
            with self._lock:
                self._write(job_id, {**state, "status": "running"})

            def attach(process):
                with self._lock:
                    self.running[job_id] = process
                    if (self._read(path) or {}).get("status") == "cancelled":  # cancelled while starting
                        self._kill(process)

            summary = runner.run_job(path, state["agent"], model=state.get("model"), on_process=attach)
            final = {"status": "done" if summary["ok"] else "failed", "seconds": summary["seconds"],
                     "message": summary.get("last_text"), "error": summary.get("error")}
            if not summary["ok"] and not final["error"]:
                final["error"] = "The agent stopped without producing a model that passed the size check."
        except Exception as error:  # noqa: BLE001
            final = {"status": "failed", "error": str(error)}
        finally:
            with self._changed:
                if (self._read(path) or {}).get("status") != "cancelled":
                    self._write(job_id, {**state, **final})
                del self.running[job_id]
                self._changed.notify_all()
