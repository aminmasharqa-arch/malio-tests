"""Run orchestration — iterate (engine × document) and record outputs.

Writes two artifacts under `benchmarks/runs/<run_id>-<ts>/`:

- `run_manifest.json` — the config, resolved manifest path, environment
  snapshot. Pins what was executed so the run is reproducible.
- `results.jsonl` — one line per (document, engine) with the canonical
  prediction, engine metadata, timings, and error label if any.

Errors on one document do not abort the run. Each failure gets an error
label (roadmap §7) and the loop moves on.
"""

from __future__ import annotations

import json
import platform
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmarks.ocr.config import EngineSpec, RunConfig
from benchmarks.ocr.extract import assemble_text, extract_invoice
from benchmarks.ocr.manifest import ManifestEntry
from benchmarks.ocr.types import OCRAdapter, PageImage


@dataclass
class _PlaceholderAdapter:
    """Carries engine_id for BLOCKED rows when adapter construction fails."""

    engine_id: str

    def load(self) -> None:  # pragma: no cover - never called for placeholders
        raise RuntimeError("placeholder adapter cannot load")

    def recognize(self, pages):  # pragma: no cover
        raise RuntimeError("placeholder adapter cannot recognize")


_PATH_PARAMS: dict[str, tuple[str, ...]] = {
    "tesseract": ("tessdata_dir", "tesseract_cmd"),
    "paddleocr-hebrew": ("models_dir",),
    "kraken": ("recognition_model", "segmentation_model"),
    "ironocr": ("bridge_path",),
    "abbyy": ("bridge_path",),
}


def _make_adapter(spec: EngineSpec, config_dir: Path | None = None) -> OCRAdapter:
    params = dict(spec.params)
    for key in _PATH_PARAMS.get(spec.adapter, ()):
        if params.get(key):
            # Expand ~, resolve against the config file's dir if relative,
            # so configs can reference user-level model dirs or bridges
            # alongside the config without hard-coding CWD.
            p = Path(str(params[key])).expanduser()
            if not p.is_absolute() and config_dir is not None:
                p = (config_dir / p).resolve()
            params[key] = p
    params.setdefault("engine_id", spec.id)

    if spec.adapter == "tesseract":
        from benchmarks.ocr.adapters.tesseract import TesseractAdapter
        return TesseractAdapter(**params)
    if spec.adapter == "paddleocr-hebrew":
        from benchmarks.ocr.adapters.paddleocr_hebrew import PaddleOCRHebrewAdapter
        return PaddleOCRHebrewAdapter(**params)
    if spec.adapter == "kraken":
        from benchmarks.ocr.adapters.kraken import KrakenAdapter
        return KrakenAdapter(**params)
    if spec.adapter == "ironocr":
        from benchmarks.ocr.adapters.ironocr import IronOCRAdapter
        return IronOCRAdapter(**params)
    if spec.adapter == "abbyy":
        from benchmarks.ocr.adapters.abbyy import ABBYYAdapter
        return ABBYYAdapter(**params)
    raise ValueError(f"unknown adapter: {spec.adapter}")


def _page_image(path: Path, page_index: int, source_file: str) -> PageImage:
    from PIL import Image

    with Image.open(path) as im:
        width, height = im.size
    return PageImage(
        source_file=source_file,
        page_index=page_index,
        width=width,
        height=height,
        image_path=path,
    )


def _run_one(
    adapter: OCRAdapter,
    entry: ManifestEntry,
    blocked_reason: str | None = None,
) -> dict[str, Any]:
    """Run one (engine, document). Returns a results.jsonl row.

    If `blocked_reason` is set, we skip page load / recognize / extract and
    emit a BLOCKED row — the roadmap requires every candidate to receive a
    reported status even when unavailable (§6).
    """
    row: dict[str, Any] = {
        "document_id": entry.document_id,
        "source_file": entry.source_file,
        "split": entry.split,
        "language": entry.language,
        "engine_id": adapter.engine_id,
    }
    if blocked_reason is not None:
        row["error"] = {"label": "BLOCKED", "message": blocked_reason}
        return row

    timings: dict[str, float] = {}

    try:
        pages = [
            _page_image(p, idx, entry.source_file)
            for idx, p in enumerate(entry.pages)
        ]
    except Exception as exc:
        row["error"] = {"label": "PAGE_LOAD_ERROR", "message": str(exc)}
        return row

    try:
        t0 = time.perf_counter()
        doc = adapter.recognize(pages)
        timings["recognize_ms"] = (time.perf_counter() - t0) * 1000
    except Exception as exc:
        message = str(exc)
        label = "BLOCKED" if message.startswith("BLOCKED:") else "OCR_FAILURE"
        err: dict[str, Any] = {"label": label, "message": message}
        if label == "OCR_FAILURE":
            err["traceback"] = traceback.format_exc(limit=3)
        row["error"] = err
        row["timings_ms"] = timings
        return row

    row["engine_metadata"] = doc.engine_metadata
    row["region_count"] = len(doc.regions)
    row["page_sizes"] = doc.page_sizes

    # Expose the raw text the downstream parser sees, plus trimmed region
    # geometry/confidence for later error-attribution analysis. The engine-
    # specific `raw_response` is deliberately dropped — it would bloat
    # results.jsonl and already lives in-process during the run.
    assembled = assemble_text(doc)
    row["assembled_text"] = assembled
    row["regions"] = [
        {
            "page": r.page,
            "text": r.text,
            "granularity": r.granularity,
            "confidence": r.confidence,
            "reading_order": r.reading_order,
            "polygon": r.polygon,
        }
        for r in doc.regions
    ]

    try:
        t0 = time.perf_counter()
        invoice = extract_invoice(doc, source_file=entry.source_file)
        timings["extract_ms"] = (time.perf_counter() - t0) * 1000
    except Exception as exc:
        row["error"] = {
            "label": "EXTRACT_FAILURE",
            "message": str(exc),
            "traceback": traceback.format_exc(limit=3),
        }
        row["timings_ms"] = timings
        return row

    row["prediction"] = {
        "source_file": invoice.source_file,
        "vendor_name": invoice.vendor_name,
        "business_tax_id": invoice.business_tax_id,
        "business_tax_id_valid": invoice.business_tax_id_valid,
        "invoice_number": invoice.invoice_number,
        "invoice_date": invoice.invoice_date,
        "amount_before_vat": invoice.amount_before_vat,
        "vat_amount": invoice.vat_amount,
        "total_amount": invoice.total_amount,
        "allocation_number": invoice.allocation_number,
        "extraction_notes": list(invoice.extraction_notes),
    }
    row["timings_ms"] = timings
    row["error"] = None
    return row


def _env_snapshot() -> dict[str, Any]:
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "cwd": str(Path.cwd()),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def run_benchmark(
    config: RunConfig,
    manifest_entries: list[ManifestEntry],
    runs_root: Path,
) -> Path:
    """Execute (engine × document) sequentially. Returns the run directory."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = runs_root / f"{config.run_id}-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    adapters: list[tuple[EngineSpec, OCRAdapter, str | None]] = []
    load_timings: dict[str, float] = {}
    load_errors: dict[str, str] = {}
    for spec in config.engines:
        try:
            adapter = _make_adapter(spec, config_dir=config.config_dir)
        except Exception as exc:
            msg = f"adapter construction failed: {exc}"
            print(f"warn: {spec.id} — {msg}", file=sys.stderr)
            load_timings[spec.id] = -1
            load_errors[spec.id] = msg
            # Build a placeholder so BLOCKED rows still carry the configured engine_id.
            adapter = _PlaceholderAdapter(spec.id)
            adapters.append((spec, adapter, msg))
            continue
        t0 = time.perf_counter()
        try:
            adapter.load()
            load_timings[spec.id] = (time.perf_counter() - t0) * 1000
            adapters.append((spec, adapter, None))
        except Exception as exc:
            load_timings[spec.id] = (time.perf_counter() - t0) * 1000
            msg = str(exc)
            load_errors[spec.id] = msg
            print(f"warn: {spec.id} — {msg}", file=sys.stderr)
            adapters.append((spec, adapter, msg))

    results_path = run_dir / "results.jsonl"
    ocr_text_dir = run_dir / "ocr_text"
    ocr_text_dir.mkdir(exist_ok=True)
    n_rows = 0
    with results_path.open("w", encoding="utf-8") as out:
        for entry in manifest_entries:
            for spec, adapter, blocked in adapters:
                row = _run_one(adapter, entry, blocked_reason=blocked)
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                n_rows += 1

                # Sidecar: one human-readable text file per (document × engine)
                # so you can `cat` the raw OCR for any pair without parsing
                # JSONL. Blocked / errored rows get a short status file.
                text_path = ocr_text_dir / f"{entry.document_id}__{spec.id}.txt"
                if row.get("assembled_text") is not None:
                    text_path.write_text(row["assembled_text"], encoding="utf-8")
                else:
                    err = row.get("error") or {}
                    text_path.write_text(
                        f"[{err.get('label', 'NO_TEXT')}] {err.get('message', '')}",
                        encoding="utf-8",
                    )

                status = "ok" if row.get("error") is None else row["error"]["label"]
                print(f"  {entry.document_id} × {spec.id}: {status}")

    manifest_out = run_dir / "run_manifest.json"
    with manifest_out.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "run_id": config.run_id,
                "engines": [
                    {"id": s.id, "adapter": s.adapter, "params": s.params}
                    for s in config.engines
                ],
                "document_count": len(manifest_entries),
                "result_rows": n_rows,
                "load_timings_ms": load_timings,
                "load_errors": load_errors,
                "environment": _env_snapshot(),
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    return run_dir
