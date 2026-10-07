"""Tesseract adapter — Phase 1 primary self-hosted baseline (roadmap §8.4).

Produces word-granularity `OCRRegion`s from `pytesseract.image_to_data`
TSV output. Word confidences come from the `conf` column (−1 → None).
Reading order is the TSV iteration order; downstream geometry-aware
assembly re-orders if needed. Do not use a global digit-only whitelist
(roadmap §8.4) — it destroys Hebrew labels and vendor names.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from benchmarks.ocr.types import OCRDocument, OCRRegion, PageImage


DEFAULT_TESSERACT_WINDOWS = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")


@dataclass
class TesseractAdapter:
    lang: str = "heb+eng"
    psm: int = 3
    oem: int = 1
    tessdata_dir: Path | None = None
    tesseract_cmd: Path = DEFAULT_TESSERACT_WINDOWS
    engine_id: str = ""
    _version: str = ""

    def __post_init__(self) -> None:
        if not self.engine_id:
            tag = "best" if self.tessdata_dir and "best" in self.tessdata_dir.as_posix() else "default"
            self.engine_id = f"tesseract-{tag}-{self.lang}-psm{self.psm}-oem{self.oem}"

    def load(self) -> None:
        import pytesseract

        if self.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = str(self.tesseract_cmd)
        try:
            self._version = str(pytesseract.get_tesseract_version())
        except Exception as exc:
            raise RuntimeError(
                f"tesseract not callable at {self.tesseract_cmd}: {exc}"
            ) from exc

    def recognize(self, pages: Sequence[PageImage]) -> OCRDocument:
        import pytesseract
        from pytesseract import Output

        config_parts = [f"--oem {self.oem}", f"--psm {self.psm}"]
        if self.tessdata_dir:
            config_parts.append(f'--tessdata-dir "{self.tessdata_dir}"')
        config = " ".join(config_parts)

        regions: list[OCRRegion] = []
        page_sizes: list[tuple[int, int]] = []
        raw_pages: list[dict[str, Any]] = []

        for page in pages:
            data = pytesseract.image_to_data(
                str(page.image_path),
                lang=self.lang,
                config=config,
                output_type=Output.DICT,
            )
            raw_pages.append(data)
            page_sizes.append((page.width, page.height))

            n = len(data["text"])
            order = 0
            for i in range(n):
                text = (data["text"][i] or "").strip()
                if not text:
                    continue
                try:
                    conf_raw = float(data["conf"][i])
                except (TypeError, ValueError):
                    conf_raw = -1.0
                confidence = conf_raw / 100.0 if conf_raw >= 0 else None

                x = int(data["left"][i])
                y = int(data["top"][i])
                w = int(data["width"][i])
                h = int(data["height"][i])
                polygon = (
                    (float(x), float(y)),
                    (float(x + w), float(y)),
                    (float(x + w), float(y + h)),
                    (float(x), float(y + h)),
                )
                regions.append(
                    OCRRegion(
                        page=page.page_index,
                        text=text,
                        polygon=polygon,
                        granularity="word",
                        confidence=confidence,
                        reading_order=order,
                    )
                )
                order += 1

        return OCRDocument(
            regions=regions,
            page_sizes=page_sizes,
            raw_response=raw_pages,
            engine_metadata={
                "engine_id": self.engine_id,
                "tesseract_version": self._version or "unknown",
                "lang": self.lang,
                "psm": self.psm,
                "oem": self.oem,
                "tessdata_dir": str(self.tessdata_dir) if self.tessdata_dir else None,
            },
        )
