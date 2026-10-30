"""Run a local AI agent CLI on a job. Each adapter uses the person's own login."""

import json
import os
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
PROMPTS = {name: (PROJECT / "prompts" / f"{name}.md").read_text() for name in ("modeler", "reviser")}


def prompt_for(job: Path) -> str:
    brief = json.loads((job / "brief.json").read_text())
    return PROMPTS["reviser" if brief.get("revision") else "modeler"]

# Never let a job fall back to pay-per-use API billing, and do not look like a
# nested session when started from inside one.
STRIPPED_ENV = {
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY",
    "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT",
}


def agent_env() -> dict:
    return {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV and not k.startswith("CLAUDE_CODE_")}


def _mcp_command(job: Path) -> tuple[str, list[str]]:
    return sys.executable, ["-m", "glbsmith.mcp_server", str(job)]


@dataclass
class Launch:
    args: list[str]
    stdin: str
    env: dict


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


class ClaudeAgent:
    id = "claude"
    label = "Claude Code"
    binary = "claude"
    # The CLI has no way to list models. These aliases always mean the latest of each, so they do not go stale.
    ALIASES = (("fable", "Fable"), ("opus", "Opus"), ("sonnet", "Sonnet"))

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def _configured_model(self) -> str | None:
        home = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
        return _read_json(home / "settings.json").get("model")

    def models(self) -> dict:
        options = [{"id": alias, "label": label} for alias, label in self.ALIASES]
        # Extra models offered to this account, as cached by the CLI. Undocumented, so use it only if it fits.
        cached = _read_json(Path.home() / ".claude.json").get("additionalModelOptionsCache")
        for entry in cached if isinstance(cached, list) else ():
            if isinstance(entry, dict) and isinstance(entry.get("value"), str):
                options.append({"id": entry["value"], "label": entry.get("description") or entry.get("label") or entry["value"]})
        return {"default": self._configured_model(), "options": options}

    def launch(self, job: Path, model: str | None) -> Launch:
        model = model or self._configured_model()  # job runs ignore the settings file otherwise
        command, args = _mcp_command(job)
        config = {"mcpServers": {"blender": {"command": command, "args": args, "env": {"PYTHONPATH": str(PROJECT)}}}}
        (job / "mcp.json").write_text(json.dumps(config))
        # Only the job's tools: no shell, no file access, none of the person's settings or hooks.
        cli = [self.binary, "-p", "--verbose", "--output-format", "stream-json",
               "--strict-mcp-config", "--mcp-config", str(job / "mcp.json"),
               "--tools", "", "--allowedTools", "mcp__blender__*",
               "--setting-sources", "", "--disable-slash-commands", "--no-session-persistence"]
        if model:
            cli += ["--model", model]
        return Launch(cli, prompt_for(job), agent_env())

    def parse(self, line: str) -> list[dict]:
        event = json.loads(line)
        kind = event.get("type")
        if kind == "assistant":
            out = []
            for block in event["message"].get("content", ()):
                if block.get("type") == "text" and block["text"].strip():
                    out.append({"kind": "text", "text": block["text"]})
                elif block.get("type") == "tool_use":
                    out.append({"kind": "tool", "name": block["name"].removeprefix("mcp__blender__")})
            return out
        if kind == "result":
            return [{"kind": "usage", "duration_s": (event.get("duration_ms") or 0) / 1000, "turns": event.get("num_turns"),
                     "usage": event.get("usage"), "api_equivalent_usd": event.get("total_cost_usd"),
                     "is_error": event.get("is_error"), "text": event.get("result")}]
        return []


class CodexAgent:
    id = "codex"
    label = "Codex"
    binary = "codex"

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def _configured(self) -> dict:
        path = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
        try:
            return tomllib.loads(path.read_text())
        except (OSError, tomllib.TOMLDecodeError):
            return {}

    def models(self) -> dict:
        """Models this account can choose, from the CLI's own catalog (it refreshes it when needed)."""
        options = []
        try:
            done = subprocess.run([self.binary, "debug", "models"], capture_output=True, text=True, timeout=20, env=agent_env())
            listed = [m for m in json.loads(done.stdout)["models"] if m.get("visibility") == "list"]
            listed.sort(key=lambda m: m.get("priority", 999))
            options = [{"id": m["slug"], "label": m.get("display_name") or m["slug"]} for m in listed]
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError, KeyError, TypeError):
            pass  # the person can still type a name
        return {"default": self._configured().get("model"), "options": options}

    def launch(self, job: Path, model: str | None) -> Launch:
        command, args = _mcp_command(job)
        configured = self._configured()  # only the model; plugins and other MCP servers stay out
        model = model or configured.get("model")
        effort = configured.get("model_reasoning_effort")
        env = {"PYTHONPATH": str(PROJECT)}
        cli = [self.binary, "exec", "--json", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--ignore-rules",
               "-C", str(job), "--sandbox", "read-only",
               "-c", 'approval_policy="never"',
               "-c", f"mcp_servers.blender.command={json.dumps(command)}",
               "-c", f"mcp_servers.blender.args={json.dumps(args)}",
               "-c", f"mcp_servers.blender.env={{PYTHONPATH={json.dumps(env['PYTHONPATH'])}}}",
               "-c", "mcp_servers.blender.startup_timeout_sec=60",
               "-c", "mcp_servers.blender.tool_timeout_sec=900",
               "-c", 'mcp_servers.blender.default_tools_approval_mode="approve"']
        if model:
            cli += ["-m", model]
        if effort:
            cli += ["-c", f'model_reasoning_effort="{effort}"']
        return Launch([*cli, "-"], prompt_for(job), agent_env())

    def parse(self, line: str) -> list[dict]:
        event = json.loads(line)
        kind = event.get("type")
        if kind == "item.started":
            item = event.get("item", {})
            if item.get("type") == "mcp_tool_call":
                return [{"kind": "tool", "name": item.get("tool")}]
        if kind == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message" and item.get("text"):
                return [{"kind": "text", "text": item["text"]}]
        if kind == "turn.completed":
            return [{"kind": "usage", "usage": event.get("usage")}]
        if kind in ("error", "turn.failed"):
            return [{"kind": "error", "text": json.dumps(event)[:500]}]
        return []


class FakeAgent:
    """Scripted stand-in that drives the real tools and Blender. Uses no quota. For tests and UI work."""

    id = "fake"
    label = "Fake (no AI)"

    def available(self) -> bool:
        return True

    def models(self) -> dict:
        return {"default": None, "options": []}

    def launch(self, job: Path, model: str | None) -> Launch:
        return Launch([sys.executable, "-m", "glbsmith.fake_agent", str(job)], "",
                      {**agent_env(), "PYTHONPATH": str(PROJECT)})

    def parse(self, line: str) -> list[dict]:
        return [json.loads(line)]


AGENTS = {agent.id: agent for agent in (ClaudeAgent(), CodexAgent())}
if os.environ.get("GLBSMITH_FAKE_AGENT") == "1":
    AGENTS["fake"] = FakeAgent()
