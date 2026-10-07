"""Google Cloud Vision one-off probe — NOT part of the Phase 1 benchmark.

The OCR_BENCHMARK_ROADMAP.md Phase 1 scope is six LOCAL candidates. Google
Vision is a cloud OCR service, so it does NOT belong in the main report or
in `phase1.yaml`. This probe is a side experiment to see how a hosted
service compares on the adjudicated images, after which the user decides
whether to:

  (A) Add it to the harness as a labeled "cloud control" (separate report
      section, explicitly NOT one of the six candidates); or
  (B) Formally amend §1/§3/§4 of the roadmap to allow cloud OCR and
      promote it to a seventh candidate; or
  (C) Delete this probe and keep the benchmark local-only.

Usage:
    # 1. Get an API key:
    #    https://console.cloud.google.com/apis/credentials
    #    (project → Enable Vision API → Create API key → restrict to Vision API)
    # 2. Drop into ocr-benchmarks/.env:
    #    GOOGLE_VISION_API_KEY=AIza...
    # 3. Run:
    cd ocr-benchmarks
    .venv/Scripts/python probes/google_vision_probe.py \
        --manifest benchmarks/data/manifest.phase1.jsonl \
        --out probes/runs/google-vision-<timestamp>

Pricing (as of 2026-10, verify at https://cloud.google.com/vision/pricing):
- First 1,000 units/month free (across TEXT_DETECTION / DOCUMENT_TEXT_DETECTION)
- $1.50 / 1,000 units thereafter
- This probe uses DOCUMENT_TEXT_DETECTION (one unit per image).

Output: for each manifest entry we write a sidecar text file + a JSON row
holding the raw Vision response summary, the assembled text, the canonical
IsraeliInvoice extracted by the shared parser, and a brief per-field
compare-to-ground-truth block. No results.jsonl, no benchmark_report.md —
by design this is NOT a benchmark run.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Reuse harness pieces for parity with how a real engine would be scored.
_PROBE_DIR = Path(__file__).resolve().parent
_BENCH_ROOT = _PROBE_DIR.parent  # ocr-benchmarks/
sys.path.insert(0, str(_BENCH_ROOT))

from benchmarks.ocr.env_file import loaded_from, merged_env  # noqa: E402
from benchmarks.ocr.manifest import load_manifest  # noqa: E402
from benchmarks.ocr.scorer import score_row  # noqa: E402
from benchmarks.ocr.types import OCRDocument, OCRRegion  # noqa: E402
from benchmarks.ocr.extract import extract_invoice  # noqa: E402


VISION_URL = "https://vision.googleapis.com/v1/images:annotate"
ENGINE_ID = "google-vision-document-text-detection-CLOUD-CONTROL"

# Patterns in error bodies that mean "fix your GCP setup; retrying won't help".
_FATAL_SETUP_MARKERS = (
    "billing",
    "has not been used",      # API not enabled
    "is disabled",            # API disabled
    "PERMISSION_DENIED",
    "API_NOT_ENABLED",
    "SERVICE_DISABLED",
    "UNAUTHENTICATED",
    "invalid api key",
    "api key not valid",
)


def _is_fatal_setup_error(http_code: int | None, body: str) -> bool:
    low = body.lower()
    if http_code in (401, 403, 429):
        return True
    return any(marker.lower() in low for marker in _FATAL_SETUP_MARKERS)


def _request_body(image_b64: str, language_hints: list[str] | None) -> dict[str, Any]:
    img_ctx: dict[str, Any] = {}
    if language_hints:
        img_ctx["languageHints"] = language_hints
    req: dict[str, Any] = {
        "image": {"content": image_b64},
        "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
    }
    if img_ctx:
        req["imageContext"] = img_ctx
    return {"requests": [req]}


def _call_vision_api_key(api_key: str, image_path: Path, language_hints: list[str]) -> dict[str, Any]:
    data = base64.b64encode(image_path.read_bytes()).decode("ascii")
    body = _request_body(data, language_hints)
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{VISION_URL}?key={api_key}",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


class _VisionClient:
    """Lazy wrapper so we only import the SDK when service-account auth is used."""

    def __init__(self, credentials_path: Path) -> None:
        from google.cloud import vision
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_file(str(credentials_path))
        self._vision = vision
        self._client = vision.ImageAnnotatorClient(credentials=creds)

    def document_text_detection(self, image_path: Path, language_hints: list[str]) -> dict[str, Any]:
        image = self._vision.Image(content=image_path.read_bytes())
        image_context = self._vision.ImageContext(language_hints=language_hints) if language_hints else None
        response = self._client.document_text_detection(image=image, image_context=image_context)
        # Convert the proto response into the same shape the REST path returns
        # (`{"responses": [...]}`) so downstream parsing stays identical.
        from google.protobuf.json_format import MessageToDict  # type: ignore
        single = MessageToDict(response._pb, preserving_proto_field_name=False)
        return {"responses": [single]}


def _bbox_poly_to_tuple(poly: dict[str, Any] | None) -> tuple[tuple[float, float], ...] | None:
    if not poly:
        return None
    verts = poly.get("vertices") or poly.get("normalizedVertices") or []
    if not verts:
        return None
    try:
        return tuple((float(v.get("x", 0)), float(v.get("y", 0))) for v in verts)
    except Exception:
        return None


def _vision_to_ocrdocument(response: dict[str, Any]) -> OCRDocument:
    """Convert a Vision API response into our shared OCRDocument contract."""
    annotations = (response.get("responses") or [{}])[0]
    full_annot = annotations.get("fullTextAnnotation") or {}
    pages = full_annot.get("pages") or []

    regions: list[OCRRegion] = []
    page_sizes: list[tuple[int, int]] = []
    order = 0

    for page_idx, page in enumerate(pages):
        w = int(page.get("width") or 0)
        h = int(page.get("height") or 0)
        page_sizes.append((w, h))
        for block in page.get("blocks", []) or []:
            for paragraph in block.get("paragraphs", []) or []:
                for word in paragraph.get("words", []) or []:
                    symbols = word.get("symbols", []) or []
                    text = "".join(s.get("text", "") for s in symbols).strip()
                    if not text:
                        continue
                    polygon = _bbox_poly_to_tuple(word.get("boundingBox"))
                    conf = word.get("confidence")
                    regions.append(
                        OCRRegion(
                            page=page_idx,
                            text=text,
                            polygon=polygon,
                            granularity="word",
                            confidence=float(conf) if conf is not None else None,
                            reading_order=order,
                        )
                    )
                    order += 1

    return OCRDocument(
        regions=regions,
        page_sizes=page_sizes,
        raw_response=response,
        engine_metadata={
            "engine_id": ENGINE_ID,
            "google_api_feature": "DOCUMENT_TEXT_DETECTION",
            "cloud": True,
            "scope_note": "Reference probe — not one of the six candidates (roadmap §1/§3/§4).",
        },
    )


def _assemble_text(doc: OCRDocument) -> str:
    """Minimal line assembly: Vision already returns reading-order words."""
    lines: list[list[str]] = []
    current: list[str] = []
    current_y: float | None = None
    gap = 15.0  # pixels — rough; Vision's reading order usually handles this
    for r in sorted(doc.regions, key=lambda x: x.reading_order or 0):
        if r.polygon is None:
            current.append(r.text)
            continue
        ys = [p[1] for p in r.polygon]
        y = sum(ys) / len(ys)
        if current_y is None or abs(y - current_y) <= gap:
            current.append(r.text)
            current_y = y if current_y is None else (current_y + y) / 2
        else:
            lines.append(current)
            current = [r.text]
            current_y = y
    if current:
        lines.append(current)
    return "\n".join(" ".join(ws) for ws in lines)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None,
                        help="Output dir (default: probes/runs/google-vision-<ts>)")
    parser.add_argument("--language-hints", default="he,en,ar",
                        help="Comma-separated BCP-47 language hints for Vision.")
    parser.add_argument("--max-docs", type=int, default=None,
                        help="Cap the number of documents probed.")
    args = parser.parse_args()

    env = merged_env(_BENCH_ROOT)
    env_source = loaded_from(_BENCH_ROOT)

    # Prefer a service-account JSON (GOOGLE_APPLICATION_CREDENTIALS) because
    # that's what Google recommends. Fall back to a browser-API-key string
    # (GOOGLE_VISION_API_KEY, must start with "AIza") if provided.
    sa_path_str = env.get("GOOGLE_APPLICATION_CREDENTIALS")
    sa_path: Path | None = None
    if sa_path_str:
        p = Path(sa_path_str).expanduser()
        if not p.is_absolute():
            p = (_BENCH_ROOT / p).resolve()
        if p.is_file():
            sa_path = p
        else:
            print(f"warning: GOOGLE_APPLICATION_CREDENTIALS points at a missing file: {p}", file=sys.stderr)

    api_key = env.get("GOOGLE_VISION_API_KEY")
    api_key_looks_valid = bool(api_key) and api_key.startswith("AIza") and len(api_key) >= 32

    client: _VisionClient | None = None
    use_api_key: bool = False
    if sa_path is not None:
        client = _VisionClient(sa_path)
        print(f"probe: using service account from {sa_path} (via {env_source})", file=sys.stderr)
    elif api_key_looks_valid:
        use_api_key = True
        print(f"probe: using GOOGLE_VISION_API_KEY from {env_source}", file=sys.stderr)
    else:
        print("ERROR: no usable Google Vision credentials.", file=sys.stderr)
        if api_key and not api_key_looks_valid:
            print(
                f"       GOOGLE_VISION_API_KEY in {env_source} doesn't look like a Google API key "
                f"(should start with 'AIza'). What you have may be a service-account key ID, "
                f"not an API key.",
                file=sys.stderr,
            )
        print(
            "       Preferred: set GOOGLE_APPLICATION_CREDENTIALS=<path to service-account .json> in .env.\n"
            "       Alternative: create an API key at https://console.cloud.google.com/apis/credentials "
            "and set GOOGLE_VISION_API_KEY=AIza...",
            file=sys.stderr,
        )
        return 2

    entries = load_manifest(args.manifest.resolve())
    if args.max_docs is not None:
        entries = entries[: args.max_docs]

    out_dir = (args.out or (_PROBE_DIR / "runs" / f"google-vision-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ocr_text").mkdir(exist_ok=True)

    language_hints = [h.strip() for h in args.language_hints.split(",") if h.strip()]
    results_path = out_dir / "results.jsonl"
    summary: dict[str, Any] = {
        "engine_id": ENGINE_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "manifest": str(args.manifest.resolve()),
        "language_hints": language_hints,
        "scope_note": "Cloud reference probe — NOT one of the six roadmap candidates.",
        "pricing_note": "DOCUMENT_TEXT_DETECTION: first 1k/month free, then $1.50/1k (verify current pricing at https://cloud.google.com/vision/pricing).",
        "documents_probed": 0,
        "critical_complete_ok": 0,
        "critical_complete_total": 0,
        "all_fields_complete_ok": 0,
        "all_fields_complete_total": 0,
    }

    with results_path.open("w", encoding="utf-8") as f:
        for entry in entries:
            if not entry.pages:
                print(f"  skip {entry.document_id}: no pages", file=sys.stderr)
                continue
            image_path = entry.pages[0]
            print(f"  {entry.document_id} ({image_path.name}) ...", end=" ", flush=True)
            try:
                t0 = time.perf_counter()
                if client is not None:
                    response = client.document_text_detection(image_path, language_hints)
                else:
                    response = _call_vision_api_key(api_key or "", image_path, language_hints)
                recognize_ms = (time.perf_counter() - t0) * 1000
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                print(f"HTTP {exc.code}: {body[:200]}")
                f.write(json.dumps({"document_id": entry.document_id, "error": {"label": "HTTP_ERROR", "message": f"{exc.code}: {body[:200]}"}}) + "\n")
                if _is_fatal_setup_error(exc.code, body):
                    print("probe: fatal setup error — stopping to avoid repeated failures.", file=sys.stderr)
                    break
                continue
            except Exception as exc:
                message = str(exc)
                print(f"ERROR: {message[:300]}")
                f.write(json.dumps({"document_id": entry.document_id, "error": {"label": "PROBE_FAILURE", "message": message[:1000]}}) + "\n")
                if _is_fatal_setup_error(None, message):
                    print("probe: fatal setup error — stopping to avoid repeated failures.", file=sys.stderr)
                    break
                continue

            doc = _vision_to_ocrdocument(response)
            text = _assemble_text(doc)

            # Sidecar text (same convention as the main harness)
            (out_dir / "ocr_text" / f"{entry.document_id}__google-vision.txt").write_text(text, encoding="utf-8")

            # Run the shared parser so we can compare canonical fields apples-to-apples.
            try:
                invoice = extract_invoice(doc, source_file=entry.source_file)
                prediction = {
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
            except Exception as exc:
                prediction = None
                print(f"extractor error: {exc}")
                f.write(json.dumps({
                    "document_id": entry.document_id,
                    "engine_id": ENGINE_ID,
                    "regions": len(doc.regions),
                    "recognize_ms": recognize_ms,
                    "error": {"label": "EXTRACT_FAILURE", "message": str(exc)},
                }) + "\n")
                continue

            # Score against the manifest's ground truth (same rules as the main report).
            truth = {"ground_truth": entry.ground_truth or {}, "annotation": entry.annotation or {}}
            score = score_row({"document_id": entry.document_id, "engine_id": ENGINE_ID, "prediction": prediction, "error": None}, truth)

            summary["documents_probed"] += 1
            if score.critical_complete is not None:
                summary["critical_complete_total"] += 1
                if score.critical_complete:
                    summary["critical_complete_ok"] += 1
            if score.all_fields_complete is not None:
                summary["all_fields_complete_total"] += 1
                if score.all_fields_complete:
                    summary["all_fields_complete_ok"] += 1

            row = {
                "document_id": entry.document_id,
                "engine_id": ENGINE_ID,
                "regions": len(doc.regions),
                "page_sizes": doc.page_sizes,
                "recognize_ms": recognize_ms,
                "prediction": prediction,
                "scoring": {
                    "critical_complete": score.critical_complete,
                    "all_fields_complete": score.all_fields_complete,
                    "digit_accuracy": score.digit_accuracy,
                    "reference_digit_count": score.reference_digit_count,
                    "fields": [
                        {
                            "field": o.field,
                            "presence": o.presence,
                            "scorable": o.scorable,
                            "predicted": o.predicted,
                            "expected": o.expected,
                            "correct": o.correct,
                            "error_label": o.error_label,
                        }
                        for o in score.outcomes
                    ],
                },
                "error": None,
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"ok  {len(doc.regions)} regions, {recognize_ms:.0f} ms, critical={score.critical_complete}")

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print(f"probe: wrote {results_path}")
    print(f"       critical_complete: {summary['critical_complete_ok']}/{summary['critical_complete_total']}")
    print(f"       all_fields_complete: {summary['all_fields_complete_ok']}/{summary['all_fields_complete_total']}")
    print(f"       raw text per doc:   {out_dir / 'ocr_text'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
