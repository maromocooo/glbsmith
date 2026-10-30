"""Run one job from the command line:

    python -m glbsmith.run photo.png --width 35 --depth 35 --height 155 --agent claude
"""

import argparse
import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path

from PIL import Image, ImageOps

from .agents import AGENTS

JOBS = Path(os.environ.get("GLBSMITH_JOBS", Path(__file__).resolve().parent.parent / "jobs"))
DEFAULT_TIMEOUT = 45 * 60
MAX_IMAGE_SIDE = 2048


def create_job(image, width: float, depth: float, height: float, *, name: str | None = None,
               jobs: Path | None = None, allow_unsandboxed: bool = False) -> Path:
    """New job folder. The photo is re-encoded, which also drops EXIF data such as GPS position."""
    with Image.open(image) as source:
        photo = ImageOps.exif_transpose(source)
        photo = photo.convert("RGBA" if photo.mode in ("RGBA", "LA", "P") else "RGB")
    photo.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    job = (jobs or JOBS) / uuid.uuid4().hex[:12]
    job.mkdir(parents=True)
    photo.save(job / "reference.png")
    (job / "brief.json").write_text(json.dumps({
        "name": name, "width": width, "depth": depth, "height": height, "image": "reference.png",
        **({"allow_unsandboxed": True} if allow_unsandboxed else {}),
    }))
    return job


def run_job(job: Path, agent_id: str, *, model: str | None = None, timeout: float = DEFAULT_TIMEOUT, on_event=None, on_process=None) -> dict:
    agent = AGENTS[agent_id]
    launch = agent.launch(job, model)
    started = time.time()
    summary = {"agent": agent_id, "tools": {}, "usage": None, "ok": False}

    def emit(event: dict) -> None:
        with (job / "agent.jsonl").open("a") as log:
            log.write(json.dumps({"t": round(time.time() - started, 1), **event}) + "\n")
        if event["kind"] == "tool":
            summary["tools"][event["name"]] = summary["tools"].get(event["name"], 0) + 1
        elif event["kind"] == "usage":
            summary["usage"] = event
        elif event["kind"] == "text":
            summary["last_text"] = event["text"]
        elif event["kind"] == "error":
            summary["error"] = event["text"]
        if on_event:
            on_event(event)

    with (job / "agent.stderr").open("w") as stderr:
        process = subprocess.Popen(launch.args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
                                   text=True, env=launch.env, cwd=job, start_new_session=True, bufsize=1)
        if on_process:
            on_process(process)
        timer = threading.Timer(timeout, process.kill)
        timer.start()
        try:
            process.stdin.write(launch.stdin)
            process.stdin.close()
            with (job / "agent.raw.jsonl").open("w") as raw:
                for line in process.stdout:
                    raw.write(line)
                    raw.flush()
                    try:
                        events = agent.parse(line)
                    except (json.JSONDecodeError, KeyError, TypeError):
                        continue
                    for event in events:
                        emit(event)
            process.wait()
        finally:
            timer.cancel()

    report = job / "report.json"
    summary["seconds"] = round(time.time() - started)
    summary["exit_code"] = process.returncode
    if report.exists():
        summary["report"] = json.loads(report.read_text())
        summary["ok"] = bool(summary["report"].get("ok")) and (job / "model.glb").exists()
    (job / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--width", type=float, required=True, help="cm")
    parser.add_argument("--depth", type=float, required=True, help="cm")
    parser.add_argument("--height", type=float, required=True, help="cm")
    parser.add_argument("--agent", choices=AGENTS, default="claude")
    parser.add_argument("--model")
    parser.add_argument("--name")
    args = parser.parse_args()
    job = create_job(args.image, args.width, args.depth, args.height, name=args.name)
    print(f"job: {job}", flush=True)
    summary = run_job(job, args.agent, model=args.model, on_event=lambda e: print(json.dumps(e, ensure_ascii=False)[:300], flush=True))
    print(json.dumps({k: summary.get(k) for k in ("ok", "seconds", "exit_code", "tools", "usage")}, indent=2))


if __name__ == "__main__":
    main()
