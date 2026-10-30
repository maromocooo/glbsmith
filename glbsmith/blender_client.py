"""Starts the Blender worker for a job and talks to it over JSON lines."""

import json
import os
import subprocess
import threading
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
MARKER = "@@GLBSMITH "


def blender_binary() -> str:
    return os.environ.get("BLENDER_BIN") or str(PROJECT / ".tools" / "blender" / "blender")


class BlenderError(RuntimeError):
    pass


class BlenderWorker:
    def __init__(self, job: Path, allow_unsandboxed: bool = False):
        self.job = Path(job).resolve()
        self.allow_unsandboxed = allow_unsandboxed
        self.process: subprocess.Popen | None = None
        self.info: dict = {}
        self._lock = threading.Lock()
        self._next = 0

    def start(self) -> dict:
        tmp = self.job / "tmp"
        tmp.mkdir(exist_ok=True)
        # Nothing from the user's environment (API keys, tokens) reaches Blender.
        env = {
            "PATH": "/usr/bin:/bin", "HOME": str(self.job), "TMPDIR": str(tmp), "LANG": "C.UTF-8",
            "BLENDER_USER_CONFIG": str(tmp / "config"), "BLENDER_USER_SCRIPTS": str(tmp / "scripts"),
            "BLENDER_USER_DATAFILES": str(tmp / "data"), "BLENDER_USER_EXTENSIONS": str(tmp / "extensions"),
        }
        args = [blender_binary(), "--background", "--factory-startup", "--offline-mode", "--disable-autoexec",
                "--python-exit-code", "3", "--python", str(PROJECT / "blender" / "worker.py"), "--", str(self.job)]
        if self.allow_unsandboxed:
            args.append("unsandboxed")
        cpuset = os.environ.get("GLBSMITH_CPUSET")
        if cpuset:
            args = ["taskset", "-c", cpuset, *args]
        self.log = open(self.job / "blender.log", "a")  # noqa: SIM115 - lives as long as the process
        self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
                                        text=True, env=env, cwd=self.job, bufsize=1)
        self.info = self._read(timeout=120)
        if not self.info.get("ready"):
            raise BlenderError(f"Blender did not start: {self.info}")
        return self.info

    def _read(self, timeout: float) -> dict:
        timer = threading.Timer(timeout, self.stop)
        timer.start()
        try:
            for line in self.process.stdout:
                if line.startswith(MARKER):
                    return json.loads(line[len(MARKER):])
                self.log.write(line)
            raise BlenderError(f"Blender stopped (see {self.job / 'blender.log'}).")
        finally:
            timer.cancel()

    def call(self, command: str, timeout: float = 300, **args) -> dict:
        with self._lock:
            if self.process is None or self.process.poll() is not None:
                self.start()  # picks up from the saved model.blend
            self._next += 1
            self.process.stdin.write(json.dumps({"id": self._next, "command": command, "args": args}) + "\n")
            self.process.stdin.flush()
            try:
                answer = self._read(timeout)
            except BlenderError as error:
                raise BlenderError(f"{command} did not finish within {timeout:.0f} s or Blender stopped; "
                                   f"the last saved state is kept. {error}") from error
        if "error" in answer:
            raise BlenderError(answer["error"])
        return answer["result"]

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.kill()
