"""OCR adapter contract.

Every engine adapter (Tesseract best/fast, RivoksLab paddleocr-hebrew,
Kraken, IronOCR, ABBYY FineReader Engine) returns an `OCRDocument` built
from `OCRRegion`s. The downstream `extract_invoice(ocr_document, source_file)`
entry point consumes this type and produces the canonical invoice JSON
(see mdfilesforprompts/prompt.md).

Keep engine-specific serialization inside the adapter; only common fields
belong on `OCRRegion`/`OCRDocument`. Preserve raw responses on
`OCRDocument.raw_response` for audit — the canonical object stays clean.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence


@dataclass(frozen=True)
class PageImage:
    """One rasterized page handed to an OCR engine.

    `source_file` is the original filename (e.g. "invoice_10032.pdf") and is
    preserved end-to-end so canonical JSON can echo it. `page_index` is
    0-based within the source. PDFs are rendered once to disk and all engines
    read the same `image_path` — see roadmap §6 (common raster arm).
    """

    source_file: str
    page_index: int
    width: int
    height: int
    image_path: Path


@dataclass(frozen=True)
class OCRRegion:
    page: int
    text: str
    polygon: tuple[tuple[float, float], ...] | None
    granularity: str  # "word" | "line" | "block"
    confidence: float | None
    reading_order: int | None


@dataclass
class OCRDocument:
    regions: list[OCRRegion]
    page_sizes: list[tuple[int, int]]
    raw_response: Any
    engine_metadata: dict[str, Any] = field(default_factory=dict)


class OCRAdapter(Protocol):
    """Stable contract every engine must satisfy.

    `engine_id` is a stable identifier recorded in run manifests
    (e.g. "tesseract-best-5.5.3", "paddleocr-hebrew-server-svtrv2-word").
    `load()` is called once per worker before any `recognize()` call so
    model-init cost can be measured separately from per-document latency.
    Adapters that cannot find their binary/model/trial raise from `load()`
    with a message starting `BLOCKED:` so the runner records a BLOCKED row
    instead of calling `recognize()`.
    """

    engine_id: str

    def load(self) -> None: ...

    def recognize(self, pages: Sequence[PageImage]) -> OCRDocument: ...
