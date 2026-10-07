"""Build an honest PDF ranking of all engine configs (local + cloud control).

Reads a local-engine run dir + a Google Vision probe dir, scores each row
against the manifest ground truth (via the shared scorer), and writes a
PDF with:
- Executive ranking (total correct fields / total scorable fields)
- Honest-view ranking (PRESENT-only fields — strips the inflation from
  easy ABSENT matches)
- Per-field × per-engine matrix
- Per-document winner table
- Methodology + small-n caveats

Usage:
    python probes/build_ranking_pdf.py \
        --local-run benchmarks/runs/phase1-<ts> \
        --cloud-run probes/runs/google-vision-<ts> \
        --manifest benchmarks/data/manifest.phase1.jsonl \
        --out probes/runs/<ts>/benchmark_ranking.pdf
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
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
)


# Register a Unicode-capable font that renders Hebrew (and Arabic) glyphs.
# Default Helvetica is Latin-only, so Hebrew falls back to the "missing
# glyph" box. We try a short list of common Windows + Linux Unicode fonts.
_UNICODE_FONT = "Helvetica"
_UNICODE_FONT_BOLD = "Helvetica-Bold"


def _register_unicode_font() -> None:
    global _UNICODE_FONT, _UNICODE_FONT_BOLD
    candidates = [
        # (regular path, bold path, logical name)
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
                return
            except Exception:
                continue

_PROBE_DIR = Path(__file__).resolve().parent
_BENCH_ROOT = _PROBE_DIR.parent
sys.path.insert(0, str(_BENCH_ROOT))

from benchmarks.ocr.scorer import wilson_interval  # noqa: E402


BUSINESS_FIELDS: tuple[str, ...] = (
    "vendor_name",
    "business_tax_id",
    "invoice_number",
    "invoice_date",
    "amount_before_vat",
    "vat_amount",
    "total_amount",
    "allocation_number",
)

FIELD_SHORT: dict[str, str] = {
    "vendor_name": "Vendor",
    "business_tax_id": "Tax ID",
    "invoice_number": "Invoice#",
    "invoice_date": "Date",
    "amount_before_vat": "BeforeVAT",
    "vat_amount": "VAT",
    "total_amount": "Total",
    "allocation_number": "Alloc#",
}


@dataclass
class EngineStats:
    engine_id: str
    blocked: bool = False
    total_correct: int = 0
    total_scorable: int = 0
    present_correct: int = 0
    present_scorable: int = 0
    absent_correct: int = 0
    absent_scorable: int = 0
    per_field_correct: dict[str, int] = field(default_factory=dict)
    per_field_scorable: dict[str, int] = field(default_factory=dict)
    per_field_present_correct: dict[str, int] = field(default_factory=dict)
    per_field_present_scorable: dict[str, int] = field(default_factory=dict)
    docs_run: int = 0
    docs_won: int = 0  # per-doc wins (highest critical-correct count)


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def accumulate(rows: list[dict[str, Any]], stats: dict[str, EngineStats]) -> None:
    for row in rows:
        eid = row.get("engine_id")
        if not eid:
            continue
        err = row.get("error")
        if err and err.get("label") == "BLOCKED":
            e = stats.setdefault(eid, EngineStats(engine_id=eid))
            e.blocked = True
            continue
        if err:
            continue
        e = stats.setdefault(eid, EngineStats(engine_id=eid))
        e.docs_run += 1
        for fld in (row.get("scoring") or {}).get("fields", []):
            if not fld.get("scorable"):
                continue
            fname = fld["field"]
            e.total_scorable += 1
            e.per_field_scorable[fname] = e.per_field_scorable.get(fname, 0) + 1
            if fld["presence"] == "PRESENT":
                e.present_scorable += 1
                e.per_field_present_scorable[fname] = e.per_field_present_scorable.get(fname, 0) + 1
            else:  # ABSENT
                e.absent_scorable += 1
            if fld["correct"]:
                e.total_correct += 1
                e.per_field_correct[fname] = e.per_field_correct.get(fname, 0) + 1
                if fld["presence"] == "PRESENT":
                    e.present_correct += 1
                    e.per_field_present_correct[fname] = e.per_field_present_correct.get(fname, 0) + 1
                else:
                    e.absent_correct += 1


def compute_doc_winners(local_rows: list[dict[str, Any]], cloud_rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Return {document_id: [engine_id, ...]} where listed engines tied for most-correct on that doc."""
    doc_scores: dict[str, dict[str, int]] = {}
    for row in local_rows + cloud_rows:
        if row.get("error"):
            continue
        doc = row.get("document_id")
        eid = row.get("engine_id")
        if not doc or not eid:
            continue
        correct = sum(1 for f in (row.get("scoring") or {}).get("fields", []) if f.get("scorable") and f.get("correct"))
        doc_scores.setdefault(doc, {})[eid] = correct
    winners: dict[str, list[str]] = {}
    for doc, by_engine in doc_scores.items():
        top = max(by_engine.values())
        winners[doc] = sorted([eid for eid, c in by_engine.items() if c == top])
    return winners


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local-run", type=Path, required=True)
    ap.add_argument("--cloud-run", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    local_rows = load_rows(args.local_run.resolve() / "per_document_results.jsonl")
    cloud_rows = load_rows(args.cloud_run.resolve() / "results.jsonl")

    stats: dict[str, EngineStats] = {}
    accumulate(local_rows, stats)
    accumulate(cloud_rows, stats)

    doc_winners = compute_doc_winners(local_rows, cloud_rows)
    for doc_id, winners in doc_winners.items():
        for eid in winners:
            if eid in stats:
                stats[eid].docs_won += 1 / len(winners)  # share ties

    # Count unique documents
    n_docs = len({r.get("document_id") for r in local_rows + cloud_rows if r.get("document_id")})

    # Rank: by total_correct desc, tiebreak by present_correct desc (fewer "free" ABSENT matches)
    ranked = sorted(stats.values(), key=lambda e: (-e.total_correct, -e.present_correct, e.engine_id))

    _register_unicode_font()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    pdf = SimpleDocTemplate(
        str(args.out),
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title="Malio OCR benchmark — honest ranking",
        author="Malio OCR benchmark harness",
    )
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontName=_UNICODE_FONT, fontSize=9, leading=12, alignment=TA_LEFT)
    small = ParagraphStyle("small", parent=styles["BodyText"], fontName=_UNICODE_FONT, fontSize=8, leading=10, textColor=colors.grey)
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontName=_UNICODE_FONT_BOLD, fontSize=16, leading=20, spaceBefore=0, spaceAfter=8)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName=_UNICODE_FONT_BOLD, fontSize=12, leading=16, spaceBefore=12, spaceAfter=6)
    caveat = ParagraphStyle("caveat", parent=body, backColor=colors.whitesmoke, borderColor=colors.lightgrey, borderWidth=0.5, borderPadding=6)

    story: list[Any] = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # ---------- Title + intro ----------
    story.append(Paragraph("Malio OCR benchmark — honest ranking", h1))
    story.append(Paragraph(
        f"Generated {now} UTC · {n_docs} adjudicated phone-photo invoices · "
        f"{len([e for e in stats.values() if not e.blocked])} engine configs scored · "
        f"{len([e for e in stats.values() if e.blocked])} BLOCKED (missing license/SDK).",
        small,
    ))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        "<b>What this ranks:</b> per-field exact-match correctness, using the shared regex extractor "
        "from <code>pdf-tests/extractor.py</code>. The engine with the most correct fields across "
        "the manifest wins. Ties are listed. Fields marked UNREADABLE or AMBIGUOUS in the ground "
        "truth are excluded from denominators.",
        body,
    ))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(
        f"<b>n = {n_docs}. Wilson 95% CIs span ~45 percentage points.</b> This ranking is informative, "
        "not statistically conclusive. Roadmap §6 asks for ~100 documents to screen engineering decisions. "
        "Confidence intervals narrow materially only above n ≈ 50.",
        caveat,
    ))

    # ---------- Overall ranking ----------
    story.append(Paragraph("Ranking — all scorable fields", h2))
    story.append(Paragraph(
        "Counts both PRESENT fields (engine must extract the correct value) and ABSENT fields (engine "
        "must return null). Scoring 3/4 on allocation_number mostly reflects correctly returning null "
        "on 3 docs where no allocation exists — see the next table for a stricter view.",
        small,
    ))
    overall = [["Rank", "Engine", "Correct / Scorable", "Accuracy", "Wilson 95% CI", "Docs won"]]
    rank_num = 1
    last_correct = None
    for e in ranked:
        if e.blocked:
            overall.append(["—", e.engine_id, "BLOCKED", "n/a", "—", "—"])
            continue
        if last_correct is not None and e.total_correct == last_correct:
            r = ""
        else:
            r = str(rank_num)
        rank_num += 1
        last_correct = e.total_correct
        lo, hi = wilson_interval(e.total_correct, e.total_scorable)
        acc = f"{100 * e.total_correct / e.total_scorable:.1f}%" if e.total_scorable else "n/a"
        wn = f"[{lo*100:.1f}%, {hi*100:.1f}%]"
        overall.append([
            r, e.engine_id,
            f"{e.total_correct} / {e.total_scorable}",
            acc, wn, f"{e.docs_won:.1f}",
        ])
    t = Table(overall, colWidths=[12 * mm, 70 * mm, 32 * mm, 20 * mm, 32 * mm, 18 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#eaffea")),  # highlight #1
    ]))
    story.append(t)

    # ---------- Honest-view ranking: PRESENT only ----------
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("Honest view — PRESENT fields only (did the engine actually extract?)", h2))
    story.append(Paragraph(
        "Removes the inflation from easy ABSENT matches. This is the harder question: when a field "
        "IS on the document, does the engine recover the correct value?",
        small,
    ))
    present_ranked = sorted(stats.values(), key=lambda e: (-e.present_correct, -e.present_scorable, e.engine_id))
    present_table = [["Rank", "Engine", "Correct / Scorable (PRESENT)", "Accuracy", "Wilson 95% CI"]]
    rank_num = 1
    last_correct = None
    for e in present_ranked:
        if e.blocked:
            present_table.append(["—", e.engine_id, "BLOCKED", "n/a", "—"])
            continue
        if last_correct is not None and e.present_correct == last_correct:
            r = ""
        else:
            r = str(rank_num)
        rank_num += 1
        last_correct = e.present_correct
        lo, hi = wilson_interval(e.present_correct, e.present_scorable)
        acc = f"{100 * e.present_correct / e.present_scorable:.1f}%" if e.present_scorable else "n/a"
        wn = f"[{lo*100:.1f}%, {hi*100:.1f}%]"
        present_table.append([
            r, e.engine_id,
            f"{e.present_correct} / {e.present_scorable}",
            acc, wn,
        ])
    t = Table(present_table, colWidths=[12 * mm, 70 * mm, 42 * mm, 20 * mm, 36 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#eaffea")),
    ]))
    story.append(t)

    # ---------- Per-field matrix ----------
    story.append(PageBreak())
    story.append(Paragraph("Per-field correctness matrix", h2))
    story.append(Paragraph(
        "One cell per (engine × field). Shows correct/scorable where scorable &gt; 0, otherwise '—'. "
        "Fields excluded from this document's ground truth (UNREADABLE/AMBIGUOUS) do not count.",
        small,
    ))
    header = ["Engine"] + [FIELD_SHORT[f] for f in BUSINESS_FIELDS]
    matrix = [header]
    for e in ranked:
        row = [e.engine_id]
        if e.blocked:
            row.extend(["BLKD"] * len(BUSINESS_FIELDS))
        else:
            for f in BUSINESS_FIELDS:
                scorable = e.per_field_scorable.get(f, 0)
                correct = e.per_field_correct.get(f, 0)
                row.append(f"{correct}/{scorable}" if scorable else "—")
        matrix.append(row)
    col_widths = [70 * mm] + [12.5 * mm] * len(BUSINESS_FIELDS)
    t = Table(matrix, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
    ]))
    story.append(t)

    # ---------- Per-document winner table ----------
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("Per-document winner (critical-fields-correct)", h2))
    story.append(Paragraph(
        "Which engine extracted the most fields correctly on each document. Ties shared.",
        small,
    ))
    winner_table = [["Document", "Winning engine(s)"]]
    for doc_id in sorted(doc_winners):
        winners = doc_winners[doc_id]
        winner_table.append([doc_id, "\n".join(winners)])
    t = Table(winner_table, colWidths=[40 * mm, 140 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(t)

    # ---------- Methodology ----------
    story.append(PageBreak())
    story.append(Paragraph("Methodology", h2))
    story.append(Paragraph(
        "<b>Candidates (local — six per roadmap §4):</b> RivoksLab/paddleocr-hebrew (word, line), "
        "Tesseract + tessdata_best, Tesseract + tessdata_fast, Kraken (medium multilingual), "
        "IronOCR (2026.10.2 + Hebrew/Arabic language packs), ABBYY FineReader Engine 12.", body,
    ))
    story.append(Paragraph(
        "<b>Reference control:</b> Google Cloud Vision DOCUMENT_TEXT_DETECTION, included as a CLOUD "
        "control (not one of the six candidates — roadmap §1 forbids hosted OCR in the main benchmark). "
        "Rendered in the same tables for direct comparison, labeled with -CLOUD-CONTROL suffix.", body,
    ))
    story.append(Paragraph(
        "<b>Scoring rules (roadmap §7):</b> exact-match per field after NFC + whitespace normalization. "
        "IDs are strings (leading zeros preserved); amounts compared via Python Decimal at annotated "
        "precision; dates compared as ISO yyyy-mm-dd. No edit-distance tolerance in primary scoring.", body,
    ))
    story.append(Paragraph(
        "<b>Downstream parser:</b> all engines pipe through the same deterministic regex extractor "
        "(<code>pdf-tests/extractor.py</code>). The parser is the bottleneck for every engine — its "
        "label patterns were built for text-layer PDFs, so Hebrew OCR noise (ח↔מ, ם↔ס substitutions, "
        "dot-vs-comma decimals) breaks label matching even when the raw OCR has the right tokens.", body,
    ))
    story.append(Paragraph(
        "<b>Ground truth:</b> adjudicated by Claude reading each image directly (2026-10-07). Vendor "
        "name convention follows roadmap §2 (= the לכבוד/counterparty on the invoice). The handwritten "
        "receipt's invoice_date and the Migdal invoice's truncated vendor_name are marked "
        "AMBIGUOUS and excluded from their denominators.", body,
    ))

    story.append(Paragraph("What this ranking does NOT measure", h2))
    story.append(Paragraph(
        "• <b>Latency, RAM, GPU, cost per 1M documents, cost per correct invoice</b> — all roadmap §9 "
        "columns. Would require workload measurements + dated license quotes.<br/>"
        "• <b>Raw-text OCR quality</b> — e.g., Google Vision captures amounts in raw text that the "
        "parser then fails to extract. Judged on the final canonical JSON, not the OCR substrate.<br/>"
        "• <b>Ensemble/routing performance</b> — roadmap §5 options A/B/C routers are not yet simulated.<br/>"
        "• <b>Script-specific accuracy</b> — e.g., Arabic-only subsets. n=4 is too small.<br/>"
        "• <b>Statistical ranking</b> — Wilson CIs are too wide at n=4 to establish &quot;engine X is "
        "better than engine Y&quot; with confidence.", body,
    ))

    pdf.build(story)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
