"""RivoksLab/paddleocr-hebrew adapter — candidate #1 (roadmap §4).

Hebrew-specific page pipeline backed by ONNX recognizers (SVTRv2 CTC +
NRTR fallback cascade) with word- or line-level detection. Arabic is
UNSUPPORTED and must surface as such in the report — do not substitute
another engine for Arabic coverage here.

Install (see ocr-benchmarks/third_party/paddleocr-hebrew/README.md):

    git clone https://github.com/RivoksLab/paddleocr-hebrew \
        ocr-benchmarks/third_party/paddleocr-hebrew
    pip install -e ocr-benchmarks/third_party/paddleocr-hebrew huggingface_hub

Models (~180 MB) auto-download from `Rivok/paddleocr-hebrew` on first use
and cache under `~/.cache/huggingface`. Pass `params.models_dir` to pin a
specific snapshot (recommended for reproducible runs).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from benchmarks.ocr.types import OCRDocument, OCRRegion, PageImage


ACCESS_CHECKLIST = (
    "install: `git clone https://github.com/RivoksLab/paddleocr-hebrew "
    "ocr-benchmarks/third_party/paddleocr-hebrew && pip install -e "
    "ocr-benchmarks/third_party/paddleocr-hebrew huggingface_hub`; "
    "optionally pin models via `huggingface-cli download Rivok/paddleocr-hebrew` "
    "and set `params.models_dir` to the snapshot directory; "
    "record snapshot commit + file SHA-256s in the run manifest."
)

HF_REPO = "Rivok/paddleocr-hebrew"
_HF_PATTERNS_WORD = ("charset_v2f.txt", "word-det/*", "server-svtrv2/*")
_HF_PATTERNS_LINE = ("charset_v2f.txt", "line-det/*", "server-svtrv2/*")


@dataclass
class PaddleOCRHebrewAdapter:
    detection: str = "word"              # "word" | "line" — within-candidate config
    recognizer: str = "server-svtrv2"
    models_dir: Path | None = None       # if None, auto-download from HF
    providers: tuple[str, ...] | None = None  # None → onnxruntime default
    engine_id: str = ""
    _ocr: object = field(default=None, init=False, repr=False)
    _resolved_models_dir: Path | None = field(default=None, init=False, repr=False)
    _version: str = field(default="", init=False, repr=False)

    def __post_init__(self) -> None:
        if self.detection not in {"word", "line"}:
            raise ValueError(f"detection must be 'word' or 'line', got {self.detection!r}")
        if not self.engine_id:
            self.engine_id = f"paddleocr-hebrew-{self.recognizer}-{self.detection}"

    def load(self) -> None:
        try:
            from paddleocr_hebrew import HebrewOCR
        except ImportError as exc:
            raise RuntimeError(
                f"BLOCKED: paddleocr-hebrew not importable ({exc}). {ACCESS_CHECKLIST}"
            ) from exc

        try:
            import importlib.metadata as _im
            self._version = _im.version("paddleocr-hebrew")
        except Exception:
            self._version = "unknown"

        models_dir = self._resolve_models_dir()
        if models_dir is None:
            raise RuntimeError(
                f"BLOCKED: paddleocr-hebrew models_dir unresolved. {ACCESS_CHECKLIST}"
            )
        self._resolved_models_dir = Path(models_dir)

        providers = list(self.providers) if self.providers else ["CPUExecutionProvider"]
        factory = HebrewOCR.line if self.detection == "line" else HebrewOCR.word
        self._ocr = factory(str(models_dir), providers=providers)

    def _resolve_models_dir(self) -> Path | None:
        """Return a usable models_dir, auto-downloading from HF if needed."""
        if self.models_dir and Path(self.models_dir).exists():
            return Path(self.models_dir)
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise RuntimeError(
                f"BLOCKED: huggingface_hub not installed and no local models_dir. "
                f"Install with `pip install huggingface_hub` or set params.models_dir. {exc}"
            ) from exc

        patterns = _HF_PATTERNS_LINE if self.detection == "line" else _HF_PATTERNS_WORD
        snapshot = snapshot_download(repo_id=HF_REPO, allow_patterns=list(patterns))
        return Path(snapshot)

    def recognize(self, pages: Sequence[PageImage]) -> OCRDocument:
        if self._ocr is None:
            raise RuntimeError("PaddleOCRHebrewAdapter.recognize called before load()")

        regions: list[OCRRegion] = []
        page_sizes: list[tuple[int, int]] = []
        raw_pages: list[dict[str, Any]] = []

        for page in pages:
            with _open_image_size(page.image_path) as (width, height):
                page_sizes.append((width, height))

            # The HebrewOCR pipeline supports PDFs natively (via render_page),
            # but our harness has already handed us a rendered image path, so
            # we always take the image branch for consistent timing.
            result = self._ocr.read(str(page.image_path))  # type: ignore[attr-defined]

            raw_pages.append({
                "meta": result.get("meta"),
                "n_lines": len(result.get("lines", [])),
                "n_words": len(result.get("words", [])),
            })

            # Prefer word granularity for the shared downstream parser —
            # it already handles reading-order assembly from word positions.
            order = 0
            for w in result.get("words", []):
                text = str(w.get("text") or "").strip()
                if not text:
                    continue
                polygon = _bbox_to_polygon(w.get("bbox"))
                regions.append(
                    OCRRegion(
                        page=page.page_index,
                        text=text,
                        polygon=polygon,
                        granularity="word",
                        confidence=None,  # pipeline filters < conf_threshold internally
                        reading_order=order,
                    )
                )
                order += 1

            if order == 0:
                # Fallback to line granularity if no words survived filtering.
                for line in result.get("lines", []):
                    text = str(line.get("text") or "").strip()
                    if not text:
                        continue
                    polygon = _bbox_to_polygon(line.get("bbox"))
                    regions.append(
                        OCRRegion(
                            page=page.page_index,
                            text=text,
                            polygon=polygon,
                            granularity="line",
                            confidence=None,
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
                "paddleocr_hebrew_version": self._version,
                "detection": self.detection,
                "recognizer": self.recognizer,
                "models_dir": str(self._resolved_models_dir) if self._resolved_models_dir else None,
                "providers": list(self.providers) if self.providers else ["CPUExecutionProvider"],
            },
        )


class _ImageSize:
    def __init__(self, size: tuple[int, int]) -> None:
        self.size = size

    def __enter__(self) -> tuple[int, int]:
        return self.size

    def __exit__(self, *exc: Any) -> None:
        return None


def _open_image_size(path: Path) -> _ImageSize:
    from PIL import Image
    with Image.open(path) as im:
        return _ImageSize(im.size)


def _bbox_to_polygon(bbox: Any) -> tuple[tuple[float, float], ...] | None:
    """Convert an axis-aligned [x1, y1, x2, y2] bbox to a 4-point polygon."""
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return None
    return (
        (x1, y1),
        (x2, y1),
        (x2, y2),
        (x1, y2),
    )
