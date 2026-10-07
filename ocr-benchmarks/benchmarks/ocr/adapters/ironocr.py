"""IronOCR adapter — candidate #5 (roadmap §4).

Local .NET library (Tesseract-derived recognition + preprocessing/structured
output). Python drives it via a local C# worker/executable that accepts page
image paths and returns a JSON payload with text/regions/confidence (see
roadmap §8.5 — IronOCR bullet). No per-page metering, but a commercial
deployment license is required and must be recorded with its applicable
usage terms. The bridge returns OCR text/layout **only** — business-field
extraction stays in our Python parser.

Until a bridge executable + licensed IronOcr package is wired, `load()`
raises `BLOCKED:` so the runner records a BLOCKED row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from benchmarks.ocr.types import OCRDocument, PageImage


ACCESS_CHECKLIST = (
    "build the C# worker exposing IronOcr with Hebrew/Arabic/English packs; "
    "obtain a commercial license and record the quote/terms; "
    "set params.bridge_path to the compiled worker executable; "
    "pin IronOcr package version, runtime, and language-pack identifiers."
)


@dataclass
class IronOCRAdapter:
    bridge_path: Path | None = None
    lang: str = "Hebrew,Arabic,English"
    engine_id: str = ""
    _proc_cfg: dict = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.engine_id:
            self.engine_id = "ironocr-heb-ara-eng"

    def load(self) -> None:
        if self.bridge_path is None or not Path(self.bridge_path).exists():
            raise RuntimeError(
                f"BLOCKED: ironocr bridge_path missing. {ACCESS_CHECKLIST}"
            )
        self._proc_cfg = {"bridge": str(self.bridge_path), "lang": self.lang}

    def recognize(self, pages: Sequence[PageImage]) -> OCRDocument:
        raise NotImplementedError(
            "ironocr recognize() not implemented — adapter is BLOCKED-stub only."
        )
