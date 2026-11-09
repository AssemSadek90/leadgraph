"""Load a gitignored `.env` (KEY=VALUE lines) into the process environment.

Keys still reach the code only through environment variables (CLAUDE.md rule 7); the file
just saves typing `setx` on every machine. Existing environment variables win.
"""

from __future__ import annotations

import os
from pathlib import Path

from leadgraph.config import REPO_ROOT


def parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def load_env_file(path: Path | None = None, override: bool = False) -> list[str]:
    """Set variables from the file; returns the names that were set (never the values)."""
    path = path or REPO_ROOT / ".env"
    if not path.is_file():
        return []
    loaded = []
    for key, value in parse_env(path.read_text(encoding="utf-8-sig")).items():
        if override or not os.environ.get(key):
            os.environ[key] = value
            loaded.append(key)
    return loaded
