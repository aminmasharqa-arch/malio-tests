"""Kraken adapter — candidate #4 (roadmap §4).

Target: Kraken 7.1 + pinned multilingual medium recognition model (Zenodo
record 21788410) + bundled default baseline segmentation model
(`kraken/blla.mlmodel` shipped with the Kraken package).

The recognition model is Apache-2.0 (Zenodo 21788410). All three required
scripts (Hebrew/Arabic/English) appear in the project evaluation; transfer
to modern invoice photos is unmeasured and this benchmark will measure it.

Pin: Kraken package version, recognition model SHA-256, segmentation model
(bundled with the package — pinned via Kraken version), device, precision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from benchmarks.ocr.types import OCRDocument, OCRRegion, PageImage


ACCESS_CHECKLIST = (
    "pip install kraken==7.1; "
    "download multilingual medium.safetensors (DOI 10.5281/zenodo.21788410) "
    "via `kraken get 10.5281/zenodo.21788410`; "
    "record its SHA-256 and set params.recognition_model to the full file path."
)


def _bundled_segmentation_model() -> Path:
    from importlib import resources
    return Path(str(resources.files("kraken").joinpath("blla.mlmodel")))


@dataclass
class KrakenAdapter:
    recognition_model: Path | None = None
    segmentation_model: Path | None = None  # None → bundled blla.mlmodel
    device: str = "cpu"
    text_direction: str = "horizontal-rl"  # Hebrew/Arabic default
    engine_id: str = ""
    _rec: object = field(default=None, init=False, repr=False)
    _seg: object = field(default=None, init=False, repr=False)
    _version: str = field(default="", init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.engine_id:
            self.engine_id = f"kraken-medium-{self.device}"

    def load(self) -> None:
        if self.recognition_model is None or not Path(self.recognition_model).exists():
            raise RuntimeError(
                f"BLOCKED: kraken recognition_model missing. {ACCESS_CHECKLIST}"
            )
        try:
            import kraken  # noqa: F401
            from kraken.tasks import RecognitionTaskModel, SegmentationTaskModel
        except ImportError as exc:
            raise RuntimeError(
                f"BLOCKED: kraken package not importable ({exc}). {ACCESS_CHECKLIST}"
            ) from exc

        try:
            import importlib.metadata as _im
            self._version = _im.version("kraken")
        except Exception:
            self._version = "unknown"

        seg_path = Path(self.segmentation_model) if self.segmentation_model else _bundled_segmentation_model()
        if not seg_path.exists():
            raise RuntimeError(
                f"BLOCKED: kraken segmentation_model missing at {seg_path}. {ACCESS_CHECKLIST}"
            )

        self._rec = RecognitionTaskModel.load_model(str(self.recognition_model))
        self._seg = SegmentationTaskModel.load_model(str(seg_path))

    def recognize(self, pages: Sequence[PageImage]) -> OCRDocument:
        if self._rec is None or self._seg is None:
            raise RuntimeError("KrakenAdapter.recognize called before load()")

        from PIL import Image
        from kraken.configs import RecognitionInferenceConfig, SegmentationInferenceConfig

        regions: list[OCRRegion] = []
        page_sizes: list[tuple[int, int]] = []
        raw_pages: list[dict[str, Any]] = []

        seg_cfg = SegmentationInferenceConfig(text_direction=self.text_direction)
        rec_cfg = RecognitionInferenceConfig()

        for page in pages:
            with Image.open(page.image_path) as im:
                im = im.convert("RGB")
                page_sizes.append(im.size)

                segmentation = self._seg.predict(im=im, config=seg_cfg)
                order = 0
                line_records: list[dict[str, Any]] = []
                for record in self._rec.predict(im=im, segmentation=segmentation, config=rec_cfg):
                    text = (getattr(record, "prediction", "") or "").strip()
                    if not text:
                        continue
                    polygon = _polygon_of(record)
                    confidences = getattr(record, "confidences", None)
                    confidence = (
                        float(sum(confidences) / len(confidences))
                        if confidences
                        else None
                    )
                    regions.append(
                        OCRRegion(
                            page=page.page_index,
                            text=text,
                            polygon=polygon,
                            granularity="line",
                            confidence=confidence,
                            reading_order=order,
                        )
                    )
                    line_records.append({"text": text, "confidence": confidence})
                    order += 1
            raw_pages.append({"line_count": len(line_records)})

        return OCRDocument(
            regions=regions,
            page_sizes=page_sizes,
            raw_response=raw_pages,
            engine_metadata={
                "engine_id": self.engine_id,
                "kraken_version": self._version,
                "recognition_model": str(self.recognition_model),
                "segmentation_model": str(self.segmentation_model or _bundled_segmentation_model()),
                "device": self.device,
                "text_direction": self.text_direction,
            },
        )


def _polygon_of(record: Any) -> tuple[tuple[float, float], ...] | None:
    """Extract a polygon from a Kraken BaselineOCRRecord/BBoxOCRRecord."""
    line = getattr(record, "line", None)
    if line is not None:
        boundary = getattr(line, "boundary", None)
        if boundary:
            return tuple((float(x), float(y)) for x, y in boundary)
        bbox = getattr(line, "bbox", None)
        if bbox:
            x0, y0, x1, y1 = bbox
            return (
                (float(x0), float(y0)),
                (float(x1), float(y0)),
                (float(x1), float(y1)),
                (float(x0), float(y1)),
            )
    cuts = getattr(record, "cuts", None)
    if cuts:
        xs = [p[0] for seg in cuts for p in seg]
        ys = [p[1] for seg in cuts for p in seg]
        if xs and ys:
            return (
                (float(min(xs)), float(min(ys))),
                (float(max(xs)), float(min(ys))),
                (float(max(xs)), float(max(ys))),
                (float(min(xs)), float(max(ys))),
            )
    return None
