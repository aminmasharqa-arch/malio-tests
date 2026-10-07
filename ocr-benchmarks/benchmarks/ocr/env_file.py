"""Tiny `.env` loader used by bridge adapters (IronOCR, ABBYY).

Secrets like IRONOCR_LICENSE_KEY and ABBYY_FRE_DEVELOPER_SERIAL belong in
`ocr-benchmarks/.env`, which is gitignored. The harness never persists
the key value — it just forwards it to the worker subprocess env.

Scope on purpose:
- No external dependency (python-dotenv is a 60KB package for 20 lines
  of logic). This file is the whole thing.
- No os.environ mutation. We return a merged dict and callers pass it to
  subprocess.run(env=...) so the key is scoped to the worker.
- No eval, no variable interpolation, no multiline values. Lines are
  `KEY=VALUE` with optional surrounding quotes on VALUE.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


# Cap how far we walk up looking for a .env — stops us from accidentally
# picking up a .env in C:/ or $HOME when the user intended none.
_MAX_WALK = 6


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse one .env file. Bad lines are silently skipped (no eval)."""
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or not key.replace("_", "").replace(".", "").isalnum():
            continue
        value = value.strip()
        if (len(value) >= 2) and (
            (value[0] == value[-1] == '"') or (value[0] == value[-1] == "'")
        ):
            value = value[1:-1]
        out[key] = value
    return out


@lru_cache(maxsize=32)
def find_env_file(start: Path) -> Path | None:
    """Walk up from `start` looking for `.env`. Returns the first hit or None."""
    start = start.resolve()
    for i, candidate_dir in enumerate([start, *start.parents]):
        if i >= _MAX_WALK:
            break
        candidate = candidate_dir / ".env"
        if candidate.is_file():
            return candidate
    return None


def merged_env(extra_dir: Path | None = None) -> dict[str, str]:
    """Return os.environ merged with the nearest `.env` (if any).

    The real environment wins over `.env` — matches dotenv convention
    and lets a developer override the file by exporting a variable.
    """
    env = dict(os.environ)
    search_root = (extra_dir or Path.cwd()).resolve()
    env_path = find_env_file(search_root)
    if env_path is None:
        return env
    loaded = parse_env_file(env_path)
    for k, v in loaded.items():
        env.setdefault(k, v)
    return env


def loaded_from(extra_dir: Path | None = None) -> Path | None:
    """Return the path of the `.env` the harness would load, for logging."""
    search_root = (extra_dir or Path.cwd()).resolve()
    return find_env_file(search_root)
