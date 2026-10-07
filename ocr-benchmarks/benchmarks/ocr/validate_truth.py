"""Manifest / ground-truth validation (roadmap §8.6).

Checks every manifest entry for:
- required manifest keys (document_id, source_file, split, language, pages)
- strict canonical IsraeliInvoice shape on `ground_truth` when present:
  - exact key set (no extras, no missing)
  - correct types (strings for IDs, number for amounts, bool for *_valid, …)
  - `extraction_notes` is a list of strings
  - IDs preserve leading zeros (string, not int)
  - amounts are JSON numbers (int/float) or null (no NaN/∞)
- `annotation.field_presence` keys ⊂ business fields when present

Errors are collected, not raised, so a single bad entry doesn't hide the
rest. Exit code reflects whether any error was found.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CANONICAL_KEYS: tuple[str, ...] = (
    "source_file",
    "vendor_name",
    "business_tax_id",
    "business_tax_id_valid",
    "invoice_number",
    "invoice_date",
    "amount_before_vat",
    "vat_amount",
    "total_amount",
    "allocation_number",
    "extraction_notes",
)

STRING_FIELDS: tuple[str, ...] = (
    "source_file",
    "vendor_name",
    "business_tax_id",
    "invoice_number",
    "invoice_date",
    "allocation_number",
)

AMOUNT_FIELDS: tuple[str, ...] = (
    "amount_before_vat",
    "vat_amount",
    "total_amount",
)

PRESENCE_VALUES: frozenset[str] = frozenset({"PRESENT", "ABSENT", "UNREADABLE", "AMBIGUOUS"})

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


@dataclass
class ValidationResult:
    total: int = 0
    ok: int = 0
    with_truth: int = 0
    errors: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


def _validate_canonical(doc_id: str, gt: dict[str, Any], errors: list[str]) -> None:
    extra = set(gt) - set(CANONICAL_KEYS)
    missing = set(CANONICAL_KEYS) - set(gt)
    if extra:
        errors.append(f"{doc_id}: ground_truth has extra keys: {sorted(extra)}")
    if missing:
        errors.append(f"{doc_id}: ground_truth missing keys: {sorted(missing)}")

    for field_name in STRING_FIELDS:
        value = gt.get(field_name)
        if value is not None and not isinstance(value, str):
            errors.append(f"{doc_id}: {field_name!r} must be string or null, got {type(value).__name__}")

    for field_name in AMOUNT_FIELDS:
        value = gt.get(field_name)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{doc_id}: {field_name!r} must be number or null, got {type(value).__name__}")
        elif isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            errors.append(f"{doc_id}: {field_name!r} must be a finite number")

    valid = gt.get("business_tax_id_valid", "<missing>")
    if valid is not None and not isinstance(valid, bool):
        if valid != "<missing>":
            errors.append(
                f"{doc_id}: business_tax_id_valid must be bool or null, got {type(valid).__name__}"
            )

    notes = gt.get("extraction_notes")
    if notes is None:
        errors.append(f"{doc_id}: extraction_notes must be a list (empty when no notes), not null")
    elif not isinstance(notes, list) or any(not isinstance(n, str) for n in notes):
        errors.append(f"{doc_id}: extraction_notes must be list[str]")


def _validate_annotation(doc_id: str, annotation: dict[str, Any], errors: list[str]) -> None:
    presence = annotation.get("field_presence")
    if presence is None:
        return
    if not isinstance(presence, dict):
        errors.append(f"{doc_id}: annotation.field_presence must be an object")
        return
    for key, value in presence.items():
        if key not in BUSINESS_FIELDS:
            errors.append(f"{doc_id}: annotation.field_presence has unknown field {key!r}")
        if value not in PRESENCE_VALUES:
            errors.append(
                f"{doc_id}: annotation.field_presence[{key!r}] must be one of {sorted(PRESENCE_VALUES)}"
            )


def validate_manifest(manifest_path: Path) -> ValidationResult:
    result = ValidationResult()
    manifest_path = manifest_path.resolve()
    base = manifest_path.parent
    with manifest_path.open(encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            raw = raw.strip()
            if not raw or raw.startswith("#"):
                continue
            result.total += 1
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as exc:
                result.errors.append(f"line {lineno}: invalid JSON: {exc}")
                continue

            doc_id = obj.get("document_id") or f"<line {lineno}>"
            errors_before = len(result.errors)

            for key in ("document_id", "source_file", "split", "language", "pages"):
                if key not in obj:
                    result.errors.append(f"{doc_id}: missing required manifest key {key!r}")

            pages = obj.get("pages", [])
            if isinstance(pages, list):
                for p in pages:
                    pp = Path(p)
                    full = pp if pp.is_absolute() else (base / pp)
                    if not full.exists():
                        result.errors.append(f"{doc_id}: page path does not exist: {full}")

            gt = obj.get("ground_truth")
            if gt is not None:
                result.with_truth += 1
                if not isinstance(gt, dict):
                    result.errors.append(f"{doc_id}: ground_truth must be an object")
                else:
                    _validate_canonical(doc_id, gt, result.errors)
                    source = gt.get("source_file")
                    if source and obj.get("source_file") and source != obj["source_file"]:
                        result.errors.append(
                            f"{doc_id}: ground_truth.source_file ({source!r}) != manifest source_file ({obj['source_file']!r})"
                        )

            annotation = obj.get("annotation")
            if annotation is not None:
                if not isinstance(annotation, dict):
                    result.errors.append(f"{doc_id}: annotation must be an object")
                else:
                    _validate_annotation(doc_id, annotation, result.errors)

            if len(result.errors) == errors_before:
                result.ok += 1
            else:
                for err in result.errors[errors_before:]:
                    result.messages.append(f"ERROR: {err}")

    return result
