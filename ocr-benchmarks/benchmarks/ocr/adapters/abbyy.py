"""ABBYY FineReader Engine adapter — candidate #6 (roadmap §4).

Commercial local OCR SDK (FineReader Engine 12 target). Hebrew/Arabic/English
language modules must be licensed. Python drives a local C#/C++ bridge that
exposes recognition and returns OCR text/layout only — the Python parser
retains all business-field extraction.

Access to this engine must be verified: installed build identifier, licensed
language modules, image-processing settings, and a dated deployment-specific
quote are all required before an unblocked run. Until that bridge is wired,
`load()` raises `BLOCKED:` so the runner records a BLOCKED row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from benchmarks.ocr.types import OCRDocument, PageImage


ACCESS_CHECKLIST = (
    "install licensed ABBYY FineReader Engine 12 build; "
    "enable Hebrew/Arabic/English language modules; "
    "build the C#/C++ bridge exporting OCR text + layout only; "
    "record build identifier, module list, image-processing settings, "
    "and a dated deployment-specific license quote; "
    "set params.bridge_path to the compiled bridge."
)


@dataclass
class ABBYYAdapter:
    bridge_path: Path | None = None
    lang: str = "Hebrew,Arabic,English"
    engine_id: str = ""
    _proc_cfg: dict = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.engine_id:
            self.engine_id = "abbyy-fre12-heb-ara-eng"

    def load(self) -> None:
        if self.bridge_path is None or not Path(self.bridge_path).exists():
            raise RuntimeError(
                f"BLOCKED: abbyy bridge_path missing. {ACCESS_CHECKLIST}"
            )
        self._proc_cfg = {"bridge": str(self.bridge_path), "lang": self.lang}

    def recognize(self, pages: Sequence[PageImage]) -> OCRDocument:
        raise NotImplementedError(
            "abbyy recognize() not implemented — adapter is BLOCKED-stub only."
        )
