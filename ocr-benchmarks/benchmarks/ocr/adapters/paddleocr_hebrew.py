"""RivoksLab/paddleocr-hebrew adapter — candidate #1 (roadmap §4).

Hebrew-specific page pipeline with downloadable ONNX models. Supports the
documented `word` and `line` detection modes as within-candidate configs
(roadmap §4 within-candidate configurations). Arabic is UNSUPPORTED and
must surface as such in the report — do not substitute another engine.

The real adapter will call `HebrewOCR.word(...)` / `HebrewOCR.line(...)`
from the installed package. Until models + bindings are present locally,
`load()` raises `BLOCKED:` so the runner records a BLOCKED row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from benchmarks.ocr.types import OCRDocument, PageImage


ACCESS_CHECKLIST = (
    "install RivoksLab/paddleocr-hebrew bindings; "
    "download pinned ONNX recognizer/detector/cascade; "
    "record model SHA-256; "
    "set params.models_dir and params.detection to 'word' or 'line'."
)


@dataclass
class PaddleOCRHebrewAdapter:
    detection: str = "word"  # "word" | "line" — within-candidate config
    recognizer: str = "server-svtrv2"
    models_dir: Path | None = None
    engine_id: str = ""
    _hebrew_ocr: object = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.detection not in {"word", "line"}:
            raise ValueError(f"detection must be 'word' or 'line', got {self.detection!r}")
        if not self.engine_id:
            self.engine_id = f"paddleocr-hebrew-{self.recognizer}-{self.detection}"

    def load(self) -> None:
        if self.models_dir is None or not Path(self.models_dir).exists():
            raise RuntimeError(
                f"BLOCKED: paddleocr-hebrew models_dir not set or missing. {ACCESS_CHECKLIST}"
            )
        try:
            from paddleocr_hebrew import HebrewOCR  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                f"BLOCKED: paddleocr-hebrew package not importable ({exc}). {ACCESS_CHECKLIST}"
            ) from exc
        self._hebrew_ocr = HebrewOCR(models_dir=str(self.models_dir))

    def recognize(self, pages: Sequence[PageImage]) -> OCRDocument:
        raise NotImplementedError(
            "paddleocr-hebrew recognize() not implemented — adapter is BLOCKED-stub only."
        )
