"""Repo inspection — locates OCR calls, normalization, regex extractors,
checksum validators, canonical serialization, and tests. Writes a module map
that seeds the benchmark report (roadmap §8.1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "runs", "results", "results-carmel"}


@dataclass(frozen=True)
class Hit:
    category: str
    path: Path
    line: int
    snippet: str


CATEGORIES: list[tuple[str, re.Pattern[str]]] = [
    (
        "OCR engine calls",
        re.compile(
            r"^\s*(?:"
            r"import\s+(?:pytesseract|cv2|paddleocr|kraken)\b"
            r"|from\s+(?:pytesseract|cv2|paddleocr|kraken)\b"
            r")"
        ),
    ),
    (
        "Hebrew/Arabic normalization",
        re.compile(
            r"\bdef\s+(?:strip_bidi\w*|strip_nikud\w*|fold_sofit\w*|"
            r"fix_quoted_abbrev\w*|reverse_tokens\w*|normalize_for_matching\w*|"
            r"strip_tatweel\w*|normalize_arabic\w*)"
        ),
    ),
    (
        "Field extraction (regex)",
        re.compile(r"\bdef\s+_extract_[a-z_]+\s*\("),
    ),
    (
        "Israeli ID checksum",
        re.compile(r"\bdef\s+validate_israeli_id\b|\bdef\s+\w*israeli_\w*id\w*\b|\bdef\s+\w*checksum\w*\b"),
    ),
    (
        "Canonical JSON schema",
        re.compile(r"\bclass\s+IsraeliInvoice\b|\bdef\s+to_json\b|\bdef\s+extract_from_(?:text|pdf)\b"),
    ),
]

TEST_FILE_RE = re.compile(r"^test_.+\.py$|_test\.py$")


def discover_python_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for path in root.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        out.append(path)
    return sorted(out)


def scan_file(path: Path) -> list[Hit]:
    hits: list[Hit] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return hits
    for i, line in enumerate(lines, start=1):
        for category, pattern in CATEGORIES:
            if pattern.search(line):
                hits.append(Hit(category=category, path=path, line=i, snippet=line.strip()))
    return hits


def find_tests(files: list[Path]) -> list[Path]:
    return [p for p in files if TEST_FILE_RE.match(p.name)]


def render_markdown(repo_root: Path, files: list[Path], hits: list[Hit], tests: list[Path]) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines: list[str] = []
    lines.append("# Malio OCR benchmark — module map")
    lines.append("")
    lines.append(f"- Generated: {now}")
    lines.append(f"- Repo root: `{repo_root}`")
    lines.append(f"- Python files scanned: {len(files)}")
    lines.append(f"- Hits: {len(hits)}")
    lines.append("")

    by_category: dict[str, list[Hit]] = {name: [] for name, _ in CATEGORIES}
    for h in hits:
        by_category[h.category].append(h)

    for category, _ in CATEGORIES:
        bucket = by_category[category]
        lines.append(f"## {category}")
        if not bucket:
            lines.append("")
            lines.append("_No matches._")
            lines.append("")
            continue
        lines.append("")
        for h in bucket:
            rel = h.path.relative_to(repo_root).as_posix()
            lines.append(f"- `{rel}:{h.line}` — `{h.snippet}`")
        lines.append("")

    lines.append("## Tests")
    lines.append("")
    if not tests:
        lines.append("_No test files found._")
    else:
        for t in tests:
            rel = t.relative_to(repo_root).as_posix()
            lines.append(f"- `{rel}`")
    lines.append("")

    lines.append("## Benchmark harness status")
    lines.append("")
    bench = repo_root / "ocr-benchmarks" / "benchmarks"
    checks = [
        ("adapters", bench / "ocr" / "adapters"),
        ("configs", bench / "configs"),
        ("data (ground truth)", bench / "data"),
    ]
    for label, p in checks:
        exists = p.exists()
        has_content = exists and any(x for x in p.iterdir() if x.name != "__init__.py")
        state = "populated" if has_content else ("empty" if exists else "missing")
        rel = p.relative_to(repo_root).as_posix()
        lines.append(f"- `{rel}` — {state}")
    lines.append("")
    return "\n".join(lines)


def run(repo_root: Path, out_path: Path | None) -> tuple[Path, int]:
    files = discover_python_files(repo_root)
    hits: list[Hit] = []
    for f in files:
        hits.extend(scan_file(f))
    tests = find_tests(files)
    report = render_markdown(repo_root, files, hits, tests)

    if out_path is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = repo_root / "ocr-benchmarks" / "benchmarks" / "runs" / f"inspect-{stamp}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "module_map.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    return out_path, len(hits)
