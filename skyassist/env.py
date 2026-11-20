"""Load a project-root `.env` file into the process environment, and notice when it changes.

Deliberately tiny: KEY=VALUE lines, `#` comments, optional single or double quotes, optional `export `.
Real environment variables always win. Keys that came from the file are remembered, so saving the file again with a
new key, a changed key or a removed key takes effect within a couple of seconds, with no restart.
"""

import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / ".env"
_state: dict = {"mtime": None, "applied": {}, "checked": 0.0}


def parse(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def load_dotenv(path: Path | None = None) -> list[str]:
    """Apply the file once and return the names it set. A missing file is not an error."""
    target = path or DEFAULT
    try:
        text = target.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    applied = []
    for key, value in parse(text).items():
        if key not in os.environ and value != "":
            os.environ[key] = value
            applied.append(key)
            if target == DEFAULT:
                _state["applied"][key] = value
    if target == DEFAULT:
        _state["mtime"] = target.stat().st_mtime
    return applied


def refresh(min_interval: float = 2.0) -> list[str]:
    """Pick up edits to the project `.env`. Returns the names that changed. Cheap: one stat, at most every few seconds."""
    now = time.monotonic()
    if now - _state["checked"] < min_interval:
        return []
    _state["checked"] = now
    try:
        mtime = DEFAULT.stat().st_mtime
    except OSError:
        mtime = None
    if mtime == _state["mtime"]:
        return []
    values: dict[str, str] = {}
    if mtime is not None:
        try:
            values = parse(DEFAULT.read_text(encoding="utf-8-sig"))
        except OSError:
            return []
    applied: dict[str, str] = _state["applied"]
    changed = []
    for key, value in values.items():
        if key in os.environ and key not in applied:
            continue  # a real environment variable owns this name
        if value == "":
            if key in applied:
                os.environ.pop(key, None)
                applied.pop(key)
                changed.append(key)
        elif os.environ.get(key) != value:
            os.environ[key] = value
            applied[key] = value
            changed.append(key)
    for key in [k for k in applied if k not in values]:
        os.environ.pop(key, None)
        applied.pop(key)
        changed.append(key)
    _state["mtime"] = mtime
    return changed


def value(name: str, default: str = "") -> str:
    """An environment value, after noticing any edit to `.env`."""
    refresh()
    return os.environ.get(name, default).strip()
