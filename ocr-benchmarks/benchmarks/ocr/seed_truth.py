"""Ground-truth seeding (roadmap §7 workflow).

Walks a folder of invoices, runs Tesseract OCR on each, pipes the result
through the shared `extract_invoice` to get canonical-shape JSON guesses,
and writes a manifest JSONL where every `annotation.field_presence` is
`UNREADABLE` + `adjudication_status: PENDING`.

The point: adjudication becomes "verify or correct 8 pre-filled fields"
instead of "transcribe from scratch." The scorer excludes `UNREADABLE`
fields from denominators, so until a human flips presence to
`PRESENT`/`ABSENT`, these stubs don't poison the field-accuracy numbers.

PDFs are rasterized once into a sidecar `rendered/` directory next to
the manifest; images are referenced in place. All paths in the output
manifest are **relative to the manifest file's directory** so manifests
remain portable across machines.
"""

from __future__ import annotations

import json
import re
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmarks.ocr.adapters.tesseract import TesseractAdapter
from benchmarks.ocr.extract import assemble_text, extract_invoice
from benchmarks.ocr.types import PageImage


IMAGE_EXTENSIONS: frozenset[str] = frozenset({".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"})
PDF_EXTENSIONS: frozenset[str] = frozenset({".pdf"})

BUSINESS_FIELDS: tuple[str, ...] = (
    "vendor_name",
    "business_tax_id",
    "invoice_number",
    "invoice_date",
    "amount_before_vat",
    "vat_amount",
    "total_amount",
    "allocation_number",
)

CANONICAL_KEYS: tuple[str, ...] = (
    "source_file",
    *BUSINESS_FIELDS[:3],
    "business_tax_id_valid",
    *BUSINESS_FIELDS[3:],
    "extraction_notes",
)


@dataclass
class SeedOptions:
    input_dir: Path
    out_path: Path
    split: str = "development"
    language: str = "heb"
    tesseract_lang: str = "heb+eng"
    pdf_dpi: int = 300
    max_docs: int | None = None
    emit_ocr_sample_lines: int = 10
    rendered_dir: Path | None = None  # defaults to out_path.parent / "rendered"
    tessdata_dir: Path | None = None
    tesseract_cmd: Path | None = None
    overwrite: bool = False
    tags: tuple[str, ...] = ()


@dataclass
class SeedResult:
    total_candidates: int = 0
    seeded: int = 0
    skipped_existing: int = 0
    errors: list[str] = field(default_factory=list)
    manifest_path: Path | None = None


_DOC_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _derive_document_id(path: Path) -> str:
    """Stem with filesystem-unfriendly characters flattened to `_`."""
    stem = path.stem
    cleaned = _DOC_ID_RE.sub("_", stem).strip("._-")
    return cleaned or "doc"


def _enumerate_inputs(input_dir: Path) -> list[Path]:
    out: list[Path] = []
    for p in sorted(input_dir.rglob("*")):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in IMAGE_EXTENSIONS or ext in PDF_EXTENSIONS:
            out.append(p)
    return out


def _load_existing_document_ids(manifest_path: Path) -> set[str]:
    ids: set[str] = set()
    if not manifest_path.exists():
        return ids
    with manifest_path.open(encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw or raw.startswith("#"):
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            doc_id = obj.get("document_id")
            if isinstance(doc_id, str):
                ids.add(doc_id)
    return ids


def _render_pdf_pages(pdf_path: Path, out_dir: Path, dpi: int) -> list[Path]:
    """Rasterize all pages of a PDF to PNG under `out_dir`. Returns written paths."""
    import pymupdf  # modern PyMuPDF import; pulled in by paddleocr-hebrew

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with pymupdf.open(str(pdf_path)) as doc:
        zoom = dpi / 72.0
        matrix = pymupdf.Matrix(zoom, zoom)
        for i, page in enumerate(doc):
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            page_path = out_dir / f"{pdf_path.stem}_p{i:03d}.png"
            pix.save(page_path.as_posix())
            written.append(page_path)
    return written


def _page_image(path: Path, page_index: int, source_file: str) -> PageImage:
    from PIL import Image

    with Image.open(path) as im:
        width, height = im.size
    return PageImage(
        source_file=source_file,
        page_index=page_index,
        width=width,
        height=height,
        image_path=path,
    )


def _canonical_from_invoice(invoice: Any, source_file: str) -> dict[str, Any]:
    """Mirror the IsraeliInvoice dataclass into the canonical JSON dict shape."""
    return {
        "source_file": source_file,
        "vendor_name": getattr(invoice, "vendor_name", None),
        "business_tax_id": getattr(invoice, "business_tax_id", None),
        "business_tax_id_valid": getattr(invoice, "business_tax_id_valid", None),
        "invoice_number": getattr(invoice, "invoice_number", None),
        "invoice_date": getattr(invoice, "invoice_date", None),
        "amount_before_vat": getattr(invoice, "amount_before_vat", None),
        "vat_amount": getattr(invoice, "vat_amount", None),
        "total_amount": getattr(invoice, "total_amount", None),
        "allocation_number": getattr(invoice, "allocation_number", None),
        "extraction_notes": list(getattr(invoice, "extraction_notes", []) or []),
    }


def _empty_canonical(source_file: str) -> dict[str, Any]:
    return {
        "source_file": source_file,
        "vendor_name": None,
        "business_tax_id": None,
        "business_tax_id_valid": None,
        "invoice_number": None,
        "invoice_date": None,
        "amount_before_vat": None,
        "vat_amount": None,
        "total_amount": None,
        "allocation_number": None,
        "extraction_notes": [],
    }


def _unreadable_presence() -> dict[str, str]:
    return {k: "UNREADABLE" for k in BUSINESS_FIELDS}


def _rel_posix(path: Path, base: Path) -> str:
    """Return `path` as a POSIX-style path relative to `base` where possible."""
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        # If path is outside base (e.g. shared drive), fall back to absolute.
        return path.as_posix()


def seed(opts: SeedOptions, *, log: Any = print) -> SeedResult:
    result = SeedResult()
    input_dir = opts.input_dir.resolve()
    if not input_dir.is_dir():
        raise FileNotFoundError(f"input_dir not found: {input_dir}")

    out_path = opts.out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_dir = out_path.parent
    rendered_dir = (opts.rendered_dir or (manifest_dir / "rendered")).resolve()

    existing_ids = set() if opts.overwrite else _load_existing_document_ids(out_path)

    # Build Tesseract adapter once; load it so first-doc latency doesn't
    # include Tesseract warm-up.
    adapter_kwargs: dict[str, Any] = {
        "lang": opts.tesseract_lang,
        "psm": 3,
        "oem": 1,
    }
    if opts.tessdata_dir:
        adapter_kwargs["tessdata_dir"] = opts.tessdata_dir
    if opts.tesseract_cmd:
        adapter_kwargs["tesseract_cmd"] = opts.tesseract_cmd
    adapter = TesseractAdapter(**adapter_kwargs)
    adapter.load()

    candidates = _enumerate_inputs(input_dir)
    result.total_candidates = len(candidates)
    log(f"seed-truth: scanning {input_dir} — {result.total_candidates} candidate file(s)")

    mode = "w" if opts.overwrite else "a"
    with out_path.open(mode, encoding="utf-8") as f:
        for path in candidates:
            if opts.max_docs is not None and result.seeded >= opts.max_docs:
                break

            document_id = _derive_document_id(path)
            source_file = path.name

            if document_id in existing_ids:
                result.skipped_existing += 1
                log(f"  skip {document_id}: already in manifest (--overwrite to redo)")
                continue

            try:
                pages = _resolve_pages(path, document_id, source_file, rendered_dir, opts.pdf_dpi)
                doc = adapter.recognize(pages)
                text = assemble_text(doc)
                try:
                    invoice = extract_invoice(doc, source_file=source_file)
                    canonical = _canonical_from_invoice(invoice, source_file)
                except Exception as exc:  # extractor failure — still emit a null stub
                    canonical = _empty_canonical(source_file)
                    canonical["extraction_notes"].append(f"extractor_error: {exc}")

                entry = _build_manifest_entry(
                    document_id=document_id,
                    source_file=source_file,
                    pages=pages,
                    manifest_dir=manifest_dir,
                    canonical=canonical,
                    ocr_text=text,
                    engine_id=adapter.engine_id,
                    opts=opts,
                )
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
                result.seeded += 1
                log(f"  seeded {document_id} ({len(pages)} page(s), {len(doc.regions)} regions)")
            except Exception as exc:
                tb = traceback.format_exc(limit=3)
                msg = f"{document_id}: {exc}\n{tb}"
                result.errors.append(msg)
                log(f"  ERROR {document_id}: {exc}", file=sys.stderr)

    result.manifest_path = out_path
    return result


def _resolve_pages(
    source: Path, document_id: str, source_file: str, rendered_dir: Path, dpi: int
) -> list[PageImage]:
    ext = source.suffix.lower()
    if ext in PDF_EXTENSIONS:
        pdf_render_dir = rendered_dir / document_id
        rendered_paths = _render_pdf_pages(source, pdf_render_dir, dpi=dpi)
        return [_page_image(p, i, source_file) for i, p in enumerate(rendered_paths)]
    if ext in IMAGE_EXTENSIONS:
        return [_page_image(source, 0, source_file)]
    raise ValueError(f"unsupported file type: {ext}")


def _build_manifest_entry(
    *,
    document_id: str,
    source_file: str,
    pages: list[PageImage],
    manifest_dir: Path,
    canonical: dict[str, Any],
    ocr_text: str,
    engine_id: str,
    opts: SeedOptions,
) -> dict[str, Any]:
    ocr_lines = [line for line in ocr_text.splitlines() if line.strip()]
    sample = ocr_lines[: max(0, opts.emit_ocr_sample_lines)]

    page_rel = [_rel_posix(p.image_path, manifest_dir) for p in pages]

    return {
        "document_id": document_id,
        "source_file": source_file,
        "split": opts.split,
        "language": opts.language,
        "pages": page_rel,
        "tags": list(opts.tags),
        "ground_truth": canonical,
        "annotation": {
            "field_presence": _unreadable_presence(),
            "adjudication_status": "PENDING",
            "seeded_by": engine_id,
            "note": (
                "Pre-populated from an unverified Tesseract OCR pass. "
                "Verify each ground_truth value against the source document, "
                "then update annotation.field_presence to PRESENT (correct) or "
                "ABSENT (field not on the document). Change adjudication_status "
                "to COMPLETE when done."
            ),
            "ocr_sample": sample,
        },
    }
