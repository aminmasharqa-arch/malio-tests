"""Bridge: OCRDocument → canonical IsraeliInvoice JSON (roadmap §8.2).

Every OCR adapter produces an `OCRDocument`. This module assembles word
regions into text and feeds the shared extractor in `pdf-tests/extractor.py`,
skipping its PyMuPDF-specific repairs because OCR text is already in reading
order.

The repository's existing field-extraction pipeline is the single downstream
path — one `extract_invoice(doc, source_file)` entry point for every engine.
Keep engine-specific logic in adapters, not here.
"""

from __future__ import annotations

import importlib.util
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any

from benchmarks.ocr.types import OCRDocument, OCRRegion


REPO_ROOT = Path(__file__).resolve().parents[3]
EXTRACTOR_PATH = REPO_ROOT / "pdf-tests" / "extractor.py"


@lru_cache(maxsize=1)
def _load_extractor() -> ModuleType:
    """Load pdf-tests/extractor.py by file path (hyphenated dir isn't importable)."""
    if not EXTRACTOR_PATH.exists():
        raise FileNotFoundError(
            f"extractor not found at {EXTRACTOR_PATH} — expected pdf-tests/extractor.py in the repo"
        )
    spec = importlib.util.spec_from_file_location("malio_pdf_tests_extractor", EXTRACTOR_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load extractor spec from {EXTRACTOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # @dataclass needs the module registered
    spec.loader.exec_module(module)
    return module


def _vertical_center(region: OCRRegion) -> float:
    if region.polygon is None:
        return 0.0
    ys = [p[1] for p in region.polygon]
    return sum(ys) / len(ys)


def _vertical_extent(region: OCRRegion) -> float:
    if region.polygon is None:
        return 0.0
    ys = [p[1] for p in region.polygon]
    return max(ys) - min(ys)


def assemble_text(doc: OCRDocument) -> str:
    """Group word regions into lines by vertical proximity, join with spaces.

    Preserves each region's reading_order within a line, so engines that
    already emit RTL words in reading order (Tesseract TSV) stay correct.
    Pages are separated by a blank line.
    """
    if not doc.regions:
        return ""

    by_page: dict[int, list[OCRRegion]] = {}
    for r in doc.regions:
        by_page.setdefault(r.page, []).append(r)

    page_lines: list[list[str]] = []
    for page_idx in sorted(by_page):
        regions = by_page[page_idx]
        regions = sorted(regions, key=lambda r: (r.reading_order if r.reading_order is not None else 0))

        heights = [_vertical_extent(r) for r in regions if _vertical_extent(r) > 0]
        typical_h = (sorted(heights)[len(heights) // 2] if heights else 20.0) or 20.0
        line_gap = typical_h * 0.6

        lines: list[list[str]] = []
        current_words: list[str] = []
        current_y: float | None = None

        for r in regions:
            y = _vertical_center(r)
            if current_y is None or abs(y - current_y) <= line_gap:
                current_words.append(r.text)
                current_y = y if current_y is None else (current_y + y) / 2
            else:
                lines.append(current_words)
                current_words = [r.text]
                current_y = y
        if current_words:
            lines.append(current_words)

        page_lines.append([" ".join(ws) for ws in lines])

    return "\n\n".join("\n".join(lines) for lines in page_lines)


def extract_invoice(doc: OCRDocument, source_file: str) -> Any:
    """One entry point every adapter funnels through. Returns an IsraeliInvoice."""
    extractor = _load_extractor()
    text = assemble_text(doc)
    return extractor.extract_from_text(
        text,
        source=source_file,
        reverse_rtl_tokens=False,
        fix_pymupdf_abbreviations=False,
    )
