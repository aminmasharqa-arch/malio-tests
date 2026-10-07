"""ABBYY FineReader Engine 12 adapter — candidate #6 (roadmap §4).

Local Windows SDK bridge: the Python adapter spawns `AbbyyWorker.exe`
(built from `ocr-benchmarks/bridges/abbyy-worker/`), pipes a JSON request
on stdin, and parses the JSON response on stdout. Business-field
extraction stays in the Python parser — this adapter only turns the
worker's regions into `OCRRegion`s so the shared pipeline can run.

The worker binds to FRE 12 via late COM (`Type.GetTypeFromProgID`). If
the SDK isn't installed/registered or if the license isn't accepted,
the worker returns a `{"error": {"label": "BLOCKED", ...}}` payload and
the adapter translates that into a BLOCKED harness row.

Licensing env vars read by the worker at startup (set either in a shell
or via your run harness):
  - ABBYY_FRE_DEVELOPER_SERIAL  — developer serial number
  - ABBYY_FRE_LICENSE_PATH      — path to a .ABBYY.LicenseKey file
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from benchmarks.ocr.env_file import merged_env
from benchmarks.ocr.types import OCRDocument, OCRRegion, PageImage


ACCESS_CHECKLIST = (
    "install licensed ABBYY FineReader Engine 12 build; "
    "enable Hebrew/Arabic/English language modules; "
    "build the worker: `cd ocr-benchmarks/bridges/abbyy-worker && "
    "dotnet publish -c Release -r win-x64 --self-contained false`; "
    "set ABBYY_FRE_DEVELOPER_SERIAL (or ABBYY_FRE_LICENSE_PATH) and "
    "params.bridge_path; record build identifier, module list, "
    "image-processing settings, and a dated deployment-specific license quote."
)


@dataclass
class ABBYYAdapter:
    bridge_path: Path | None = None
    lang: str = "Hebrew,English"
    detect_rotation: bool | None = None
    timeout_s: float = 600.0
    engine_id: str = ""
    _version: str = field(default="", init=False, repr=False)
    _prog_id: str = field(default="", init=False, repr=False)
    _env: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.engine_id:
            self.engine_id = "abbyy-fre12-heb-ara-eng"

    def load(self) -> None:
        if self.bridge_path is None or not Path(self.bridge_path).exists():
            raise RuntimeError(
                f"BLOCKED: abbyy bridge_path missing. {ACCESS_CHECKLIST}"
            )
        self._env = merged_env(Path(self.bridge_path).parent)
        # Probe with empty-pages request so missing SDK/license surfaces
        # fast without having to feed an image. The worker returns
        # REQUEST_EMPTY (normal) when it can start, or BLOCKED when the
        # SDK can't be loaded — we propagate the latter untouched.
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
                f"abbyy worker returned non-JSON output: {probe.stdout!r}; stderr={probe.stderr!r}"
            ) from exc
        err = payload.get("error") or {}
        label = err.get("label")
        if label == "BLOCKED":
            raise RuntimeError(f"BLOCKED: {err.get('message') or 'abbyy worker reported BLOCKED'}")
        if label not in (None, "REQUEST_EMPTY"):
            raise RuntimeError(
                f"abbyy worker init error: {label}: {err.get('message', '')}"
            )
        self._version = payload.get("abbyy_engine_version") or "unknown"
        self._prog_id = payload.get("engine_prog_id") or ""

    def recognize(self, pages: Sequence[PageImage]) -> OCRDocument:
        if self.bridge_path is None:
            raise RuntimeError("ABBYYAdapter.recognize called before load()")

        request: dict[str, Any] = {
            "pages": [
                {"page_index": p.page_index, "image_path": str(p.image_path)}
                for p in pages
            ],
            "languages": [s.strip() for s in self.lang.split(",") if s.strip()],
        }
        if self.detect_rotation is not None:
            request["options"] = {"detect_rotation": bool(self.detect_rotation)}

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
                f"abbyy worker produced empty stdout; exit={proc.returncode}; stderr={proc.stderr!r}"
            )
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"abbyy worker returned non-JSON: {proc.stdout!r}") from exc

        err = payload.get("error")
        if err:
            # BLOCKED labels from the worker (missing SDK, license, etc.)
            # map 1:1 to our harness BLOCKED signal so the report puts
            # ABBYY in the same bucket as other access-blocked candidates.
            if err.get("label") == "BLOCKED":
                raise RuntimeError(f"BLOCKED: {err.get('message') or 'abbyy worker reported BLOCKED'}")
            raise RuntimeError(f"abbyy worker error [{err.get('label')}]: {err.get('message')}")

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
                "abbyy_engine_version": payload.get("abbyy_engine_version") or self._version,
                "engine_prog_id": payload.get("engine_prog_id") or self._prog_id,
                "worker_version": payload.get("worker_version"),
                "lang": self.lang,
                "bridge_path": str(self.bridge_path),
                "license_env_present": bool(
                    (self._env or os.environ).get("ABBYY_FRE_DEVELOPER_SERIAL")
                    or (self._env or os.environ).get("ABBYY_FRE_LICENSE_PATH")
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
    if c < 0:
        return None
    return min(c, 1.0)
