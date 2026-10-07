"""IronOCR adapter — candidate #5 (roadmap §4).

Local .NET bridge: the Python adapter spawns `IronOcrWorker.exe` (built
from `ocr-benchmarks/bridges/ironocr-worker/`), pipes a JSON request on
stdin, and parses the JSON response on stdout. Business-field extraction
stays in the Python parser — this adapter only turns worker regions into
`OCRRegion`s so the shared pipeline can run.

Licensing: the worker reads `IRONOCR_LICENSE_KEY` from its environment at
startup. Set it before `python -m benchmarks.ocr run ...`. No per-page
metering, but a dated commercial deployment quote is required for the
cost model (§9) — this adapter does not read or forward license keys.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from benchmarks.ocr.env_file import loaded_from, merged_env
from benchmarks.ocr.types import OCRDocument, OCRRegion, PageImage


ACCESS_CHECKLIST = (
    "build the worker: `cd ocr-benchmarks/bridges/ironocr-worker && "
    "dotnet publish -c Release -r win-x64 --self-contained false`; "
    "obtain a commercial IronOcr license and record the quote/terms; "
    "set env IRONOCR_LICENSE_KEY and params.bridge_path to the published "
    "`IronOcrWorker.exe`; pin IronOcr package version + language packs."
)


@dataclass
class IronOCRAdapter:
    bridge_path: Path | None = None
    lang: str = "Hebrew,English"
    read_barcodes: bool | None = None
    timeout_s: float = 300.0
    engine_id: str = ""
    _version: str = field(default="", init=False, repr=False)
    _env: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.engine_id:
            self.engine_id = "ironocr-heb-ara-eng"

    def load(self) -> None:
        if self.bridge_path is None or not Path(self.bridge_path).exists():
            raise RuntimeError(
                f"BLOCKED: ironocr bridge_path missing. {ACCESS_CHECKLIST}"
            )
        self._env = merged_env(Path(self.bridge_path).parent)
        env_path = loaded_from(Path(self.bridge_path).parent)
        if env_path and self._env.get("IRONOCR_LICENSE_KEY"):
            print(
                f"ironocr: using IRONOCR_LICENSE_KEY from {env_path}",
                file=sys.stderr,
            )
        # Smoke the worker with an empty request so we fail fast if the exe
        # can't start (missing .NET runtime, missing IronOcr assemblies, …).
        probe = subprocess.run(
            [str(self.bridge_path)],
            input=json.dumps({"pages": []}),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=self.timeout_s,
            env=self._env,
        )
        try:
            payload = json.loads(probe.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"ironocr worker returned non-JSON output: {probe.stdout!r}; stderr={probe.stderr!r}"
            ) from exc
        err = payload.get("error") or {}
        label = err.get("label")
        # REQUEST_EMPTY is the expected response for the empty-pages probe —
        # anything else means the worker couldn't initialize.
        if label not in (None, "REQUEST_EMPTY"):
            raise RuntimeError(
                f"ironocr worker init error: {label}: {err.get('message', '')}"
            )
        self._version = payload.get("ironocr_version") or "unknown"

    def recognize(self, pages: Sequence[PageImage]) -> OCRDocument:
        if self.bridge_path is None:
            raise RuntimeError("IronOCRAdapter.recognize called before load()")

        request = {
            "pages": [
                {"page_index": p.page_index, "image_path": str(p.image_path)}
                for p in pages
            ],
            "languages": [s.strip() for s in self.lang.split(",") if s.strip()],
        }
        if self.read_barcodes is not None:
            request["options"] = {"read_barcodes": bool(self.read_barcodes)}

        proc = subprocess.run(
            [str(self.bridge_path)],
            input=json.dumps(request, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=self.timeout_s,
            env=self._env or None,
        )
        if not proc.stdout:
            raise RuntimeError(
                f"ironocr worker produced empty stdout; exit={proc.returncode}; stderr={proc.stderr!r}"
            )

        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"ironocr worker returned non-JSON: {proc.stdout!r}") from exc

        err = payload.get("error")
        if err:
            message = err.get("message") or ""
            # Translate IronOcr's license-required runtime error into a
            # BLOCKED signal so the harness surfaces it the same way as
            # the missing-bridge check (roadmap §6).
            if "License Required" in message or "license key" in message.lower():
                raise RuntimeError(
                    f"BLOCKED: ironocr production license required. Set the IRONOCR_LICENSE_KEY environment "
                    f"variable before running the harness. IronOcr message: {message.splitlines()[0] if message else ''}"
                )
            raise RuntimeError(f"ironocr worker error [{err.get('label')}]: {message}")

        regions: list[OCRRegion] = []
        page_sizes: list[tuple[int, int]] = []
        for page in payload.get("pages", []):
            page_sizes.append((int(page.get("width", 0)), int(page.get("height", 0))))
            for r in page.get("regions") or []:
                polygon = _polygon_from(r.get("polygon"))
                regions.append(
                    OCRRegion(
                        page=int(page.get("page_index", 0)),
                        text=str(r.get("text") or "").strip(),
                        polygon=polygon,
                        granularity=str(r.get("granularity") or "word"),
                        confidence=_confidence_from(r.get("confidence")),
                        reading_order=r.get("reading_order"),
                    )
                )

        return OCRDocument(
            regions=regions,
            page_sizes=page_sizes,
            raw_response=payload,
            engine_metadata={
                "engine_id": self.engine_id,
                "ironocr_version": payload.get("ironocr_version") or self._version,
                "worker_version": payload.get("worker_version"),
                "lang": self.lang,
                "bridge_path": str(self.bridge_path),
                "license_env_present": bool(
                    (self._env or os.environ).get("IRONOCR_LICENSE_KEY")
                ),
                "worker_timings_ms": payload.get("timings_ms"),
            },
        )


def _polygon_from(raw: Any) -> tuple[tuple[float, float], ...] | None:
    if not isinstance(raw, list) or not raw:
        return None
    try:
        return tuple((float(x), float(y)) for x, y in raw)
    except (TypeError, ValueError):
        return None


def _confidence_from(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        c = float(raw)
    except (TypeError, ValueError):
        return None
    # Worker already normalizes to [0,1]; clamp defensively.
    if c < 0:
        return None
    return min(c, 1.0)
