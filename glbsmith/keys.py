"""API keys the person chose to use, kept outside the project and readable only by them.

A key set in the environment also counts, so people who already export one need
not enter it. It only ever reaches an agent chosen as "(API)": subscription agents
never get a key, so nobody is billed per use by surprise.
"""

import json
import os
import tempfile
from pathlib import Path

PROVIDERS = {
    "anthropic": {"environment": "ANTHROPIC_API_KEY"},
    "openai": {"environment": "OPENAI_API_KEY"},
}


def _path() -> Path:
    base = os.environ.get("GLBSMITH_CONFIG") or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "glbsmith"
    return Path(base) / "keys.json"


def _saved() -> dict:
    try:
        data = json.loads(_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def get(provider: str) -> str | None:
    """The key to use: the one saved here, or else the environment's."""
    return _saved().get(provider) or os.environ.get(PROVIDERS[provider]["environment"]) or None


def save(provider: str, key: str | None) -> None:
    """Store a key, or forget it when `key` is empty."""
    keys = _saved()
    if key:
        keys[provider] = key
    else:
        keys.pop(provider, None)
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Created private and then moved into place, so the key is never briefly readable by others.
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".keys-")
    with os.fdopen(descriptor, "w") as file:
        json.dump(keys, file)
    os.replace(temporary, path)


def describe() -> dict:
    """What the page may know. Never the key: only its source and last characters."""
    saved = _saved()
    described = {}
    for provider, info in PROVIDERS.items():
        key = get(provider)
        described[provider] = {
            "set": bool(key),
            "source": None if not key else "saved" if saved.get(provider) else "environment",
            "ends_with": key[-4:] if key and len(key) >= 12 else None,
            "environment": info["environment"],
        }
    return described
