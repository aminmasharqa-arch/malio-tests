"""Run config schema and loader.

A config file names a run and lists the engines to execute. Each engine
spec has an `adapter` discriminator (which code to run) and a `params`
dict passed through to the adapter constructor. Keep this flat — the
benchmark pins versions and model identities in the run manifest, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class EngineSpec:
    id: str
    adapter: str  # "tesseract" | "paddleocr-hebrew" | "kraken" | "ironocr" | "abbyy"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunConfig:
    run_id: str
    manifest: Path | None  # optional default — CLI --manifest overrides
    engines: tuple[EngineSpec, ...]


def load_config(config_path: Path) -> RunConfig:
    config_path = config_path.resolve()
    with config_path.open(encoding="utf-8") as f:
        obj = yaml.safe_load(f) or {}
    try:
        engines = tuple(
            EngineSpec(id=e["id"], adapter=e["adapter"], params=dict(e.get("params", {})))
            for e in obj["engines"]
        )
    except KeyError as exc:
        raise ValueError(f"{config_path}: missing required key {exc}") from exc
    if not engines:
        raise ValueError(f"{config_path}: at least one engine required")

    manifest_raw = obj.get("manifest")
    manifest: Path | None = None
    if manifest_raw:
        p = Path(manifest_raw)
        manifest = p if p.is_absolute() else (config_path.parent / p).resolve()

    return RunConfig(
        run_id=obj.get("run_id", config_path.stem),
        manifest=manifest,
        engines=engines,
    )
