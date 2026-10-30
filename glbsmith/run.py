"""Run one job from the command line:

    python -m glbsmith.run photo.png [more.png ...] --width 35 --depth 35 --height 155 --agent claude
"""

import argparse
import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .agents import AGENTS
from .overlay import highlight

JOBS = Path(os.environ.get("GLBSMITH_JOBS", Path(__file__).resolve().parent.parent / "jobs"))
DEFAULT_TIMEOUT = 45 * 60
MAX_IMAGE_SIDE = 2048


def create_job(images, width: float, depth: float, height: float, *, name: str | None = None,
               jobs: Path | None = None, allow_unsandboxed: bool = False) -> Path:
    """New job folder from (photo, painted mask or None) pairs; the first photo is the main one.

    Photos are re-encoded, which also drops EXIF data such as GPS position.
    """
    job = (jobs or JOBS) / uuid.uuid4().hex[:12]
    job.mkdir(parents=True)
    entries = []
    for index, (image, painted) in enumerate(images):
        with Image.open(image) as source:
            photo = ImageOps.exif_transpose(source)
            photo = photo.convert("RGBA" if photo.mode in ("RGBA", "LA", "P") else "RGB")
        photo.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        entry = {"file": f"reference_{index}.png", "mask": None, "view": f"reference_{index}.png"}
        photo.save(job / entry["file"])
        if painted is not None:
            with Image.open(painted) as source:
                # A transparent canvas stores the paint in alpha; a plain image in brightness.
                drawn = source.getchannel("A") if "A" in source.getbands() else source.convert("L")
                area = np.asarray(drawn.resize(photo.size, Image.NEAREST)) > 127
            if area.any():
                entry["mask"] = f"reference_{index}_mask.png"
                entry["view"] = f"reference_{index}_view.png"
                Image.fromarray(area.astype(np.uint8) * 255).save(job / entry["mask"])
                on_white = Image.new("RGB", photo.size, (255, 255, 255))
                on_white.paste(photo.convert("RGBA"), mask=photo.convert("RGBA").getchannel("A"))
                highlight(on_white, area).save(job / entry["view"])
        entries.append(entry)
    (job / "brief.json").write_text(json.dumps({
        "name": name, "width": width, "depth": depth, "height": height, "images": entries,
        **({"allow_unsandboxed": True} if allow_unsandboxed else {}),
    }))
    return job


def brief_images(brief: dict) -> list[dict]:
    """The photos of a job; jobs from before several photos had just one "image"."""
    if brief.get("images"):
        return brief["images"]
    return [{"file": brief["image"], "mask": None, "view": brief["image"]}]


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
    parser.add_argument("images", type=Path, nargs="+", help="the first photo is the main one")
    parser.add_argument("--width", type=float, required=True, help="cm")
    parser.add_argument("--depth", type=float, required=True, help="cm")
    parser.add_argument("--height", type=float, required=True, help="cm")
    parser.add_argument("--agent", choices=AGENTS, default="claude")
    parser.add_argument("--model")
    parser.add_argument("--name")
    args = parser.parse_args()
    job = create_job([(image, None) for image in args.images], args.width, args.depth, args.height, name=args.name)
    print(f"job: {job}", flush=True)
    summary = run_job(job, args.agent, model=args.model, on_event=lambda e: print(json.dumps(e, ensure_ascii=False)[:300], flush=True))
    print(json.dumps({k: summary.get(k) for k in ("ok", "seconds", "exit_code", "tools", "usage")}, indent=2))


if __name__ == "__main__":
    main()
