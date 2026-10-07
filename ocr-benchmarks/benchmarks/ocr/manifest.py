"""Manifest schema and loader.

Each line of a manifest JSONL file describes one document. Paths are
resolved relative to the manifest file's directory so manifests can move
between hosts. Ground truth is optional — the `run` command works without
it; `report` consumes it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


Split = str  # "development" | "validation" | "holdout" | custom


@dataclass(frozen=True)
class ManifestEntry:
    document_id: str
    source_file: str
    split: Split
    language: str  # "heb" | "ara" | "eng" | "mixed" | ...
    pages: tuple[Path, ...]
    ground_truth: dict[str, Any] | None = None  # strict canonical IsraeliInvoice shape
    annotation: dict[str, Any] | None = None    # free-form adjudication metadata (roadmap §7)
    tags: tuple[str, ...] = ()


def _as_path(raw: str, base: Path) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else (base / p).resolve()


def load_manifest(manifest_path: Path, *, split: Split | None = None) -> list[ManifestEntry]:
    """Read JSONL manifest. If `split` is given, only entries matching it are returned."""
    manifest_path = manifest_path.resolve()
    base = manifest_path.parent
    entries: list[ManifestEntry] = []
    with manifest_path.open(encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            raw = raw.strip()
            if not raw or raw.startswith("#"):
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{manifest_path}:{lineno}: invalid JSON: {exc}") from exc
            try:
                entry = ManifestEntry(
                    document_id=obj["document_id"],
                    source_file=obj["source_file"],
                    split=obj["split"],
                    language=obj["language"],
                    pages=tuple(_as_path(p, base) for p in obj["pages"]),
                    ground_truth=obj.get("ground_truth"),
                    annotation=obj.get("annotation"),
                    tags=tuple(obj.get("tags", ())),
                )
            except KeyError as exc:
                raise ValueError(f"{manifest_path}:{lineno}: missing required key {exc}") from exc
            if split is not None and entry.split != split:
                continue
            entries.append(entry)
    return entries
