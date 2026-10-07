"""Detailed per-engine PDF report.

For every engine (sorted by field-extraction success), shows ALL documents
with:
- Raw assembled OCR text (what the parser actually sees)
- Extracted canonical JSON (prediction)
- Ground truth
- Per-field ✓/✗ comparison

Executive ranking at the top. Caveats about n=4 in the methodology.

Usage:
    python probes/build_detailed_pdf.py \\
        --local-run benchmarks/runs/phase1-<ts> \\
        --cloud-run probes/runs/google-vision-<ts> \\
        --manifest benchmarks/data/manifest.phase1.jsonl \\
        --out docs/benchmark_detailed_<date>.pdf
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


_PROBE_DIR = Path(__file__).resolve().parent
_BENCH_ROOT = _PROBE_DIR.parent
sys.path.insert(0, str(_BENCH_ROOT))

from benchmarks.ocr.manifest import load_manifest  # noqa: E402


BUSINESS_FIELDS: tuple[str, ...] = (
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


_UNICODE_FONT = "Helvetica"
_UNICODE_FONT_BOLD = "Helvetica-Bold"
_MONO_FONT = "Courier"


def _register_fonts() -> None:
    global _UNICODE_FONT, _UNICODE_FONT_BOLD, _MONO_FONT
    candidates = [
        (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf", "Arial"),
        (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf", "SegoeUI"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans"),
        ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "Arial"),
    ]
    for regular, bold, name in candidates:
        if Path(regular).is_file():
            try:
                pdfmetrics.registerFont(TTFont(name, regular))
                if Path(bold).is_file():
                    pdfmetrics.registerFont(TTFont(f"{name}-Bold", bold))
                    _UNICODE_FONT_BOLD = f"{name}-Bold"
                else:
                    _UNICODE_FONT_BOLD = name
                _UNICODE_FONT = name
                break
            except Exception:
                continue

    mono_candidates = [
        (r"C:\Windows\Fonts\consola.ttf", "Consolas"),
        (r"C:\Windows\Fonts\cour.ttf", "Courier"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "DejaVuSansMono"),
    ]
    for path, name in mono_candidates:
        if Path(path).is_file():
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                _MONO_FONT = name
                return
            except Exception:
                continue


@dataclass
class EngineRun:
    engine_id: str
    blocked: bool = False
    rows_by_doc: dict[str, dict[str, Any]] = field(default_factory=dict)  # document_id → row
    total_correct: int = 0
    total_scorable: int = 0
    present_correct: int = 0
    present_scorable: int = 0


def _load_rows(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def _accumulate(rows: list[dict[str, Any]], engines: dict[str, EngineRun]) -> None:
    for row in rows:
        eid = row.get("engine_id")
        if not eid:
            continue
        err = row.get("error")
        if err and err.get("label") == "BLOCKED":
            e = engines.setdefault(eid, EngineRun(engine_id=eid))
            e.blocked = True
            # Still record the row so we can show "BLOCKED" per document.
            doc = row.get("document_id")
            if doc:
                e.rows_by_doc[doc] = row
            continue
        if err:
            continue
        e = engines.setdefault(eid, EngineRun(engine_id=eid))
        doc = row.get("document_id")
        if doc:
            e.rows_by_doc[doc] = row
        for fld in (row.get("scoring") or {}).get("fields", []):
            if not fld.get("scorable"):
                continue
            e.total_scorable += 1
            if fld["presence"] == "PRESENT":
                e.present_scorable += 1
            if fld["correct"]:
                e.total_correct += 1
                if fld["presence"] == "PRESENT":
                    e.present_correct += 1


def _ocr_text_for(run_dir: Path, document_id: str, engine_id: str) -> str:
    """Load the raw sidecar text file; return '' if missing."""
    path = run_dir / "ocr_text" / f"{document_id}__{engine_id}.txt"
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        return f"<could not read {path}: {exc}>"


def _format_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        if not value:
            return "[]"
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _truncate(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n… (truncated, {len(s) - limit} more chars)"


def _escape_html(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local-run", type=Path, required=True)
    ap.add_argument("--cloud-run", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--raw-text-limit", type=int, default=2500, help="Max chars of raw OCR text to include per doc×engine.")
    args = ap.parse_args()

    _register_fonts()

    local_dir = args.local_run.resolve()
    cloud_dir = args.cloud_run.resolve()
    manifest_path = args.manifest.resolve()

    local_rows = _load_rows(local_dir / "per_document_results.jsonl")
    cloud_rows = _load_rows(cloud_dir / "results.jsonl")

    engines: dict[str, EngineRun] = {}
    _accumulate(local_rows, engines)
    _accumulate(cloud_rows, engines)

    entries = load_manifest(manifest_path)
    docs_ordered = [e.document_id for e in entries]
    gt_by_doc = {e.document_id: (e.ground_truth or {}, e.annotation or {}) for e in entries}

    # Rank by present_correct (hardest view), tiebreak by total_correct
    ranked = sorted(
        engines.values(),
        key=lambda e: (e.blocked, -e.present_correct, -e.total_correct, e.engine_id),
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    pdf = SimpleDocTemplate(
        str(args.out),
        pagesize=A4,
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title="Malio OCR benchmark — per-engine detail",
        author="Malio OCR benchmark harness",
    )

    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontName=_UNICODE_FONT, fontSize=9, leading=12, alignment=TA_LEFT)
    small = ParagraphStyle("small", parent=styles["BodyText"], fontName=_UNICODE_FONT, fontSize=7.5, leading=10, textColor=colors.grey)
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontName=_UNICODE_FONT_BOLD, fontSize=16, leading=20, spaceBefore=0, spaceAfter=6)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName=_UNICODE_FONT_BOLD, fontSize=13, leading=16, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#1f3a5f"))
    h3 = ParagraphStyle("h3", parent=styles["Heading3"], fontName=_UNICODE_FONT_BOLD, fontSize=10, leading=13, spaceBefore=6, spaceAfter=2)
    cell_body = ParagraphStyle("cell_body", parent=body, fontSize=8, leading=10)
    cell_mono = ParagraphStyle("cell_mono", parent=body, fontName=_MONO_FONT, fontSize=7.5, leading=9.5)

    story: list[Any] = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # ------------ Title + Summary ------------
    n_docs = len(docs_ordered)
    n_engines_ok = len([e for e in engines.values() if not e.blocked])
    n_engines_blocked = len([e for e in engines.values() if e.blocked])

    story.append(Paragraph("Malio OCR benchmark — per-engine detail", h1))
    story.append(Paragraph(
        f"Generated {now} UTC · {n_docs} adjudicated phone-photo invoices · "
        f"{n_engines_ok} engine configs run · {n_engines_blocked} BLOCKED",
        small,
    ))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        "This report shows the complete raw-OCR output and extracted canonical JSON for every engine × "
        "document pair, ranked by field-extraction success. For each engine section, you can see exactly "
        "what the engine produced and compare it to the adjudicated ground truth, field by field.",
        body,
    ))

    story.append(Paragraph("Executive ranking", h2))
    story.append(Paragraph(
        "Primary rank: PRESENT fields correctly extracted (the hard question — did the engine recover a "
        "value that was on the document?). Tiebreak: total correct including ABSENT matches.",
        small,
    ))

    rank_table = [["Rank", "Engine", "PRESENT ✓/scorable", "ALL ✓/scorable", "PRESENT acc.", "ALL acc."]]
    rank_num = 1
    last_present = None
    for e in ranked:
        if e.blocked:
            rank_table.append(["—", e.engine_id, "BLOCKED", "BLOCKED", "n/a", "n/a"])
            continue
        if last_present is not None and e.present_correct == last_present:
            r = ""
        else:
            r = str(rank_num)
        rank_num += 1
        last_present = e.present_correct
        p_acc = f"{100 * e.present_correct / e.present_scorable:.1f}%" if e.present_scorable else "n/a"
        t_acc = f"{100 * e.total_correct / e.total_scorable:.1f}%" if e.total_scorable else "n/a"
        rank_table.append([
            r, e.engine_id,
            f"{e.present_correct} / {e.present_scorable}",
            f"{e.total_correct} / {e.total_scorable}",
            p_acc, t_acc,
        ])

    t = Table(rank_table, colWidths=[14 * mm, 72 * mm, 32 * mm, 28 * mm, 20 * mm, 20 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTNAME", (0, 0), (-1, 0), _UNICODE_FONT_BOLD),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("FONTNAME", (0, 1), (-1, -1), _UNICODE_FONT),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#eaffea")),
    ]))
    story.append(t)

    # ------------ Per-engine sections ------------
    for e_idx, engine in enumerate(ranked, 1):
        story.append(PageBreak())

        header_bits = [f"#{e_idx} — {engine.engine_id}"]
        if engine.blocked:
            header_bits.append("[BLOCKED]")
        story.append(Paragraph(" ".join(header_bits), h1))

        if engine.blocked:
            # Pull a BLOCKED message if we have one
            msg = ""
            for row in engine.rows_by_doc.values():
                err = row.get("error") or {}
                if err.get("label") == "BLOCKED":
                    msg = err.get("message", "")
                    break
            story.append(Paragraph(
                "This engine did not run on any document in this benchmark. The scaffold is in place, "
                "but access is blocked (SDK not installed, license missing, or similar).",
                body,
            ))
            if msg:
                story.append(Paragraph("Reason:", h3))
                story.append(Paragraph(_escape_html(msg), cell_body))
            continue

        story.append(Paragraph(
            f"PRESENT: {engine.present_correct}/{engine.present_scorable} · "
            f"ALL (incl. ABSENT): {engine.total_correct}/{engine.total_scorable}",
            small,
        ))

        # Which engine's run_dir holds its sidecars?
        run_dir = cloud_dir if engine.engine_id.endswith("-CLOUD-CONTROL") else local_dir

        for doc_id in docs_ordered:
            row = engine.rows_by_doc.get(doc_id)
            gt, annot = gt_by_doc.get(doc_id, ({}, {}))
            presence_map = (annot or {}).get("field_presence") or {}

            story.append(Spacer(1, 4 * mm))
            story.append(Paragraph(f"Document: {doc_id}", h2))

            if row is None:
                story.append(Paragraph("<i>No result row for this document.</i>", body))
                continue

            err = row.get("error")
            if err:
                lbl = err.get("label", "ERROR")
                story.append(Paragraph(f"<b>{lbl}</b>: {_escape_html(err.get('message', '')[:400])}", body))
                continue

            prediction = row.get("prediction") or {}

            # Per-field compare table
            compare_header = ["Field", "Ground truth", "Prediction", "Match", "Presence"]
            compare = [compare_header]
            scoring_fields = {f["field"]: f for f in (row.get("scoring") or {}).get("fields", [])}
            for fld in BUSINESS_FIELDS:
                gt_val = _format_value(gt.get(fld))
                pred_val = _format_value(prediction.get(fld))
                presence = presence_map.get(fld, "")
                score_info = scoring_fields.get(fld)
                if fld in ("business_tax_id_valid", "extraction_notes"):
                    mark = "—"  # not scored
                elif score_info is None:
                    mark = "—"
                elif not score_info.get("scorable"):
                    mark = "excl."
                else:
                    mark = "✓" if score_info.get("correct") else "✗"
                compare.append([
                    fld,
                    Paragraph(_escape_html(gt_val), cell_body),
                    Paragraph(_escape_html(pred_val), cell_body),
                    mark,
                    presence,
                ])
            tbl = Table(compare, colWidths=[36 * mm, 60 * mm, 60 * mm, 14 * mm, 22 * mm], repeatRows=1)
            style_cmds = [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, 0), _UNICODE_FONT_BOLD),
                ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                ("FONTNAME", (0, 1), (0, -1), _UNICODE_FONT),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (3, 0), (3, -1), "CENTER"),
            ]
            # Color rows by match
            for i, fld in enumerate(BUSINESS_FIELDS, 1):
                score_info = scoring_fields.get(fld)
                if fld in ("business_tax_id_valid", "extraction_notes"):
                    continue
                if score_info and score_info.get("scorable"):
                    if score_info.get("correct"):
                        style_cmds.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#eaffea")))
                    else:
                        style_cmds.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#ffeaea")))
            tbl.setStyle(TableStyle(style_cmds))
            story.append(tbl)

            # Raw OCR text sidecar
            ocr_text = _ocr_text_for(run_dir, doc_id, engine.engine_id)
            story.append(Paragraph("Raw OCR text", h3))
            if ocr_text.strip():
                truncated = _truncate(ocr_text, args.raw_text_limit)
                # Preformatted preserves line breaks; wrap long lines so they don't overflow.
                story.append(Preformatted(truncated, cell_mono, maxLineLength=100))
            else:
                story.append(Paragraph("<i>(no sidecar text available)</i>", small))

            # Timings + region count
            region_count = row.get("regions") if isinstance(row.get("regions"), int) else row.get("region_count")
            timings = row.get("timings_ms") or {}
            rec_ms = timings.get("recognize_ms") or row.get("recognize_ms")
            meta_bits = []
            if region_count is not None:
                meta_bits.append(f"regions={region_count}")
            if rec_ms is not None:
                meta_bits.append(f"recognize_ms={rec_ms:.0f}")
            if meta_bits:
                story.append(Paragraph(" · ".join(meta_bits), small))

    # ------------ Methodology ------------
    story.append(PageBreak())
    story.append(Paragraph("Methodology & caveats", h2))
    story.append(Paragraph(
        "<b>Candidates (local — six per roadmap §4):</b> RivoksLab/paddleocr-hebrew (word, line), "
        "Tesseract + tessdata_best, Tesseract + tessdata_fast, Kraken (medium multilingual), "
        "IronOCR (2026.10.2 + Hebrew/Arabic language packs), ABBYY FineReader Engine 12.",
        body,
    ))
    story.append(Paragraph(
        "<b>Reference control:</b> Google Cloud Vision DOCUMENT_TEXT_DETECTION. A CLOUD control, "
        "not one of the six candidates (roadmap §1 forbids hosted OCR in the main benchmark). "
        "Rendered alongside the local engines for comparison.",
        body,
    ))
    story.append(Paragraph(
        "<b>Scoring (roadmap §7):</b> exact-match per field after NFC + whitespace normalization. "
        "IDs are strings (leading zeros preserved); amounts compared via Decimal at annotated "
        "precision; dates compared as ISO yyyy-mm-dd. Fields marked UNREADABLE or AMBIGUOUS in the "
        "ground truth are excluded from denominators (visible as 'excl.' in per-field tables).",
        body,
    ))
    story.append(Paragraph(
        "<b>Known bottleneck:</b> every engine pipes through the same deterministic regex extractor "
        "(<code>pdf-tests/extractor.py</code>). It was designed for text-layer PDFs with exact Hebrew "
        "labels and clean decimal formats, so Hebrew OCR noise and dot-vs-comma amounts cause the "
        "parser to return null even when the correct tokens are visible in the raw OCR text. "
        "Fixing the parser lifts every engine's score.",
        body,
    ))
    story.append(Paragraph(
        f"<b>n = {n_docs}.</b> Wilson 95% CIs are too wide to statistically rank engines. This report "
        "demonstrates pipeline function and surfaces per-document strengths; roadmap §6 calls for ~100 "
        "adjudicated invoices before conclusions on engine selection.",
        body,
    ))

    pdf.build(story)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
