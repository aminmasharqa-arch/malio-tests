"""
Benchmark the deterministic Israeli-invoice extractor on a folder of PDFs.

This script is a thin wrapper around `extractor.extract_from_pdf`. It writes
per-PDF JSON into results/<pdf-stem>/parsed.json plus the raw and normalized
text for debugging, and prints an aggregate field hit-rate across the batch.

Usage:
    python test_invoice_extraction.py <path-to-pdf-or-folder>
    python test_invoice_extraction.py <path> --out results-carmel
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

from extractor import (
    extract_from_text,
    extract_text,
    normalize_for_matching,
)


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_RESULTS_DIR = BASE_DIR / "results"
DEFAULT_TARGET = BASE_DIR / "invoices-pdf-testing" / "Invoices-in"

FIELDS_FOR_SCORE = (
    "vendor_name",
    "business_tax_id",
    "invoice_number",
    "invoice_date",
    "amount_before_vat",
    "vat_amount",
    "total_amount",
    "allocation_number",
)


def _safe_stem(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem)[:80]


def _process(pdf_path: Path, results_dir: Path) -> dict:
    out_dir = results_dir / _safe_stem(pdf_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        raw = extract_text(pdf_path)
    except Exception as exc:
        return {"pdf": pdf_path.name, "error": f"text extraction failed: {exc}"}

    normalized = normalize_for_matching(raw)
    (out_dir / "raw.txt").write_text(raw, encoding="utf-8")
    (out_dir / "normalized.txt").write_text(normalized, encoding="utf-8")

    invoice = extract_from_text(raw, source=pdf_path.name)
    parsed = asdict(invoice)
    (out_dir / "parsed.json").write_text(
        json.dumps(parsed, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    hits = sum(1 for k in FIELDS_FOR_SCORE if parsed.get(k) not in (None, ""))
    print(
        f"  {pdf_path.name:40s}  fields={hits}/{len(FIELDS_FOR_SCORE)}  "
        f"id={parsed.get('business_tax_id')}  "
        f"inv#={parsed.get('invoice_number')}  "
        f"date={parsed.get('invoice_date')}  "
        f"total={parsed.get('total_amount')}"
    )

    return {"pdf": pdf_path.name, "parsed": parsed, "hits": hits}


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Benchmark the Israeli-invoice extractor on a PDF or folder."
    )
    ap.add_argument(
        "path",
        nargs="?",
        default=str(DEFAULT_TARGET),
        help="PDF file or folder (default: invoices-pdf-testing/Invoices-in)",
    )
    ap.add_argument(
        "--out", "-o",
        default=str(DEFAULT_RESULTS_DIR),
        help="Output directory for per-PDF results and summary.json "
             "(default: ./results)",
    )
    args = ap.parse_args()

    target = Path(args.path).resolve()
    results_dir = Path(args.out).resolve()

    if not target.exists():
        print(f"Not found: {target}", file=sys.stderr)
        sys.exit(2)

    results_dir.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(target.rglob("*.pdf")) if target.is_dir() else [target]
    if not pdfs:
        print(f"No PDFs under: {target}", file=sys.stderr)
        sys.exit(2)

    print(f"Processing {len(pdfs)} PDF(s) from: {target}")
    print(f"Writing results to:                {results_dir}\n")
    results = [_process(p, results_dir) for p in pdfs]

    field_hits = {k: 0 for k in FIELDS_FOR_SCORE}
    for r in results:
        parsed = r.get("parsed") or {}
        for k in FIELDS_FOR_SCORE:
            if parsed.get(k) not in (None, ""):
                field_hits[k] += 1

    n = len(results)
    aggregate = {
        "pdf_count": n,
        "field_hit_rate": {
            k: f"{v}/{n} ({100 * v / n:.0f}%)" for k, v in field_hits.items()
        },
        "per_pdf": results,
    }
    (results_dir / "summary.json").write_text(
        json.dumps(aggregate, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n" + "=" * 80)
    print("AGGREGATE")
    print("=" * 80)
    print(f"PDFs:          {n}")
    print("Field hit rate:")
    for k, v in aggregate["field_hit_rate"].items():
        print(f"  {k:22s} {v}")
    print(f"\nResults dir:   {results_dir}")


if __name__ == "__main__":
    main()
