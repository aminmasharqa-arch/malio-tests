"""Kraken adapter — candidate #4 (roadmap §4).

Target: Kraken 7.1 + pinned multilingual medium recognition model (Zenodo
record 21788410) + compatible pinned segmentation model. All three required
scripts (Hebrew/Arabic/English) appear in the project evaluation but their
transfer to modern invoice photos is unmeasured — our job is to measure.

Pin: engine version, recognition model SHA-256, segmentation model SHA-256,
device (CPU first; GPU optional), and precision.

Until Kraken + weights are installed, `load()` raises `BLOCKED:` so the
runner records a BLOCKED row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from benchmarks.ocr.types import OCRDocument, PageImage


ACCESS_CHECKLIST = (
    "pip install kraken==7.1; "
    "download multilingual medium.safetensors from Zenodo 21788410 "
    "and a pinned compatible segmentation model; "
    "record both SHA-256s and set params.recognition_model + params.segmentation_model."
)


@dataclass
class KrakenAdapter:
    recognition_model: Path | None = None
    segmentation_model: Path | None = None
    device: str = "cpu"  # "cpu" | "cuda"
    engine_id: str = ""
    _rec: object = field(default=None, init=False, repr=False)
    _seg: object = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.engine_id:
            self.engine_id = f"kraken-medium-{self.device}"

    def load(self) -> None:
        if self.recognition_model is None or not Path(self.recognition_model).exists():
            raise RuntimeError(
                f"BLOCKED: kraken recognition_model missing. {ACCESS_CHECKLIST}"
            )
        if self.segmentation_model is None or not Path(self.segmentation_model).exists():
            raise RuntimeError(
                f"BLOCKED: kraken segmentation_model missing. {ACCESS_CHECKLIST}"
            )
        try:
            from kraken.lib import models, vgsl  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                f"BLOCKED: kraken package not importable ({exc}). {ACCESS_CHECKLIST}"
            ) from exc
        self._rec = models.load_any(str(self.recognition_model), device=self.device)
        self._seg = vgsl.TorchVGSLModel.load_model(str(self.segmentation_model))

    def recognize(self, pages: Sequence[PageImage]) -> OCRDocument:
        raise NotImplementedError(
            "kraken recognize() not implemented — adapter is BLOCKED-stub only."
        )
