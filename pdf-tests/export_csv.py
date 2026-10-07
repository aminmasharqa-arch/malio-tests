"""
Export a results-<batch>/summary.json into an Excel-friendly CSV.

The CSV is written as UTF-8 with a BOM so Excel opens the Hebrew columns
in RTL without a manual encoding step.

Usage:
    python export_csv.py results-carmel
    python export_csv.py results-carmel --out carmel.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


COLUMNS = (
    "pdf",
    "vendor_name",
    "business_tax_id",
    "business_tax_id_valid",
    "invoice_number",
    "invoice_date",
    "amount_before_vat",
    "vat_amount",
    "total_amount",
    "allocation_number",
    "extraction_notes",
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "results_dir",
        help="Directory containing summary.json (e.g. ./results-carmel)",
    )
    ap.add_argument(
        "--out", "-o",
        help="Output CSV path (default: <results_dir>/summary.csv)",
    )
    args = ap.parse_args()

    results_dir = Path(args.results_dir).resolve()
    summary_path = results_dir / "summary.json"
    if not summary_path.exists():
        print(f"Not found: {summary_path}", file=sys.stderr)
        sys.exit(2)

    out_path = Path(args.out).resolve() if args.out else results_dir / "summary.csv"

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    rows = summary.get("per_pdf", [])

    # utf-8-sig = UTF-8 with BOM, which Excel needs to render Hebrew RTL
    # out of the box. newline="" is required by the csv module on Windows.
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()

        for entry in rows:
            parsed = entry.get("parsed") or {}
            notes = parsed.get("extraction_notes") or []
            writer.writerow({
                "pdf": entry.get("pdf", ""),
                "vendor_name": parsed.get("vendor_name") or "",
                "business_tax_id": parsed.get("business_tax_id") or "",
                "business_tax_id_valid": parsed.get("business_tax_id_valid"),
                "invoice_number": parsed.get("invoice_number") or "",
                "invoice_date": parsed.get("invoice_date") or "",
                "amount_before_vat": parsed.get("amount_before_vat"),
                "vat_amount": parsed.get("vat_amount"),
                "total_amount": parsed.get("total_amount"),
                "allocation_number": parsed.get("allocation_number") or "",
                "extraction_notes": " | ".join(notes),
            })

    print(f"Wrote {len(rows)} rows to: {out_path}")


if __name__ == "__main__":
    main()
