"""Benchmark CLI entry point. See roadmap §8 for the full command surface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from benchmarks.ocr import inspect


def _default_repo_root() -> Path:
    # benchmarks/ocr/cli.py -> ocr -> benchmarks -> ocr-benchmarks -> malio-tests
    return Path(__file__).resolve().parents[3]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benchmarks.ocr",
        description="Malio OCR benchmark harness (Hebrew-first invoice extraction).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_inspect = sub.add_parser(
        "inspect-repo",
        help="Scan the repo and write a module map of OCR calls, normalization, extractors, tests.",
    )
    p_inspect.add_argument("--repo-root", type=Path, default=None, help="Repo root (default: auto-detect).")
    p_inspect.add_argument("--out", type=Path, default=None, help="Output markdown path (default: runs/inspect-<ts>/module_map.md).")

    p_truth = sub.add_parser("validate-truth", help="Validate ground-truth manifest consistency.")
    p_truth.add_argument("--manifest", type=Path, required=True)

    p_run = sub.add_parser("run", help="Run OCR engines over a split and record outputs.")
    p_run.add_argument("--config", type=Path, required=True)
    p_run.add_argument("--split", choices=["development", "validation", "holdout"], required=True)
    p_run.add_argument("--manifest", type=Path, default=None, help="Override manifest path from config.")
    p_run.add_argument("--runs-root", type=Path, default=None, help="Override runs/ output directory.")

    p_report = sub.add_parser("report", help="Generate benchmark report from a completed run.")
    p_report.add_argument("--run-dir", type=Path, required=True)
    p_report.add_argument("--manifest", type=Path, required=True, help="Manifest with ground_truth to score against.")
    p_report.add_argument("--out-dir", type=Path, default=None, help="Where to write the four report artifacts (default: --run-dir).")

    p_smoke = sub.add_parser(
        "smoke",
        help="Run one engine on one image and print region summary — adapter smoke test.",
    )
    p_smoke.add_argument("--engine", choices=["tesseract"], default="tesseract")
    p_smoke.add_argument("--image", type=Path, required=True)
    p_smoke.add_argument("--lang", default="heb+eng")
    p_smoke.add_argument("--psm", type=int, default=3)
    p_smoke.add_argument("--oem", type=int, default=1)
    p_smoke.add_argument("--tessdata-dir", type=Path, default=None)
    p_smoke.add_argument("--tesseract-cmd", type=Path, default=None)
    p_smoke.add_argument("--top", type=int, default=15, help="Show top-N highest-confidence words.")
    p_smoke.add_argument("--extract", action="store_true", help="Run extract_invoice and print canonical JSON.")
    p_smoke.add_argument("--show-text", action="store_true", help="Print the assembled text used for extraction.")

    return parser


def cmd_inspect_repo(args: argparse.Namespace) -> int:
    repo_root = (args.repo_root or _default_repo_root()).resolve()
    if not repo_root.exists():
        print(f"error: repo root does not exist: {repo_root}", file=sys.stderr)
        return 2
    out_path, n_hits = inspect.run(repo_root, args.out.resolve() if args.out else None)
    print(f"inspect-repo: wrote {out_path} ({n_hits} hits)")
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    from PIL import Image

    from benchmarks.ocr.adapters.tesseract import TesseractAdapter
    from benchmarks.ocr.types import PageImage

    image_path = args.image.resolve()
    if not image_path.exists():
        print(f"error: image not found: {image_path}", file=sys.stderr)
        return 2

    with Image.open(image_path) as im:
        width, height = im.size

    page = PageImage(
        source_file=image_path.name,
        page_index=0,
        width=width,
        height=height,
        image_path=image_path,
    )

    kwargs: dict = {"lang": args.lang, "psm": args.psm, "oem": args.oem}
    if args.tessdata_dir:
        kwargs["tessdata_dir"] = args.tessdata_dir.resolve()
    if args.tesseract_cmd:
        kwargs["tesseract_cmd"] = args.tesseract_cmd.resolve()

    adapter = TesseractAdapter(**kwargs)
    adapter.load()
    doc = adapter.recognize([page])

    meta = doc.engine_metadata
    print(f"engine:    {meta['engine_id']}")
    print(f"tesseract: {meta['tesseract_version']}")
    print(f"image:     {image_path.name} ({width}x{height})")
    print(f"regions:   {len(doc.regions)}")
    print(f"top {args.top} by confidence:")
    ranked = sorted(
        (r for r in doc.regions if r.confidence is not None),
        key=lambda r: r.confidence or 0.0,
        reverse=True,
    )[: args.top]
    for r in ranked:
        print(f"  conf={r.confidence:.2f}  {r.text!r}")

    if args.show_text or args.extract:
        from benchmarks.ocr.extract import assemble_text, extract_invoice

        if args.show_text:
            print("--- assembled text ---")
            print(assemble_text(doc))

        if args.extract:
            invoice = extract_invoice(doc, source_file=image_path.name)
            print("--- canonical invoice JSON ---")
            print(invoice.to_json())
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from benchmarks.ocr.config import load_config
    from benchmarks.ocr.manifest import load_manifest
    from benchmarks.ocr.run import run_benchmark

    config = load_config(args.config.resolve())
    manifest_path = (args.manifest.resolve() if args.manifest else config.manifest)
    if manifest_path is None:
        print("error: no manifest specified (pass --manifest or set `manifest:` in config)", file=sys.stderr)
        return 2
    if not manifest_path.exists():
        print(f"error: manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    entries = load_manifest(manifest_path, split=args.split)
    if not entries:
        print(f"warn: no entries in {manifest_path} matching split={args.split}", file=sys.stderr)
        return 1

    runs_root = (args.runs_root or _default_repo_root() / "ocr-benchmarks" / "benchmarks" / "runs").resolve()
    runs_root.mkdir(parents=True, exist_ok=True)

    print(f"run: {config.run_id} — {len(entries)} docs × {len(config.engines)} engines")
    print(f"  manifest: {manifest_path}")
    print(f"  split:    {args.split}")
    print(f"  runs:     {runs_root}")
    print("---")
    run_dir = run_benchmark(config, entries, runs_root)
    print("---")
    print(f"run: wrote {run_dir}")
    return 0


def cmd_validate_truth(args: argparse.Namespace) -> int:
    from benchmarks.ocr.validate_truth import validate_manifest

    manifest_path = args.manifest.resolve()
    if not manifest_path.exists():
        print(f"error: manifest not found: {manifest_path}", file=sys.stderr)
        return 2
    result = validate_manifest(manifest_path)
    for line in result.messages:
        print(line)
    print(
        f"validate-truth: {result.ok}/{result.total} entries valid, "
        f"{result.with_truth} with ground_truth, {len(result.errors)} errors"
    )
    return 0 if not result.errors else 1


def cmd_report(args: argparse.Namespace) -> int:
    from benchmarks.ocr.report import generate_report

    run_dir = args.run_dir.resolve()
    if not run_dir.exists():
        print(f"error: run dir not found: {run_dir}", file=sys.stderr)
        return 2
    manifest_path = args.manifest.resolve()
    if not manifest_path.exists():
        print(f"error: manifest not found: {manifest_path}", file=sys.stderr)
        return 2
    out_dir = args.out_dir.resolve() if args.out_dir else None
    written = generate_report(run_dir, manifest_path, out_dir)
    print(f"report: wrote four artifacts under {written}")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = _build_parser().parse_args(argv)
    if args.command == "inspect-repo":
        return cmd_inspect_repo(args)
    if args.command == "smoke":
        return cmd_smoke(args)
    if args.command == "validate-truth":
        return cmd_validate_truth(args)
    if args.command == "run":
        return cmd_run(args)
    if args.command == "report":
        return cmd_report(args)
    return 2
