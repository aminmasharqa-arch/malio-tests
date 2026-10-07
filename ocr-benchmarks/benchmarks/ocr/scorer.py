"""Scoring per roadmap §7 — exact-match rules + aggregate metrics.

Scoring is deterministic and conservative:
- IDs / invoice numbers / allocation numbers: exact string equality after
  NFC + outer whitespace stripping. No edit-distance tolerance, no integer
  conversion, no lost zeros.
- Date: exact ISO date string.
- Amounts: exact `Decimal` match at the annotated precision; no scoring
  tolerance that hides a wrong digit. Arithmetic rounding tolerance is a
  validator concern, not a scorer concern.
- Vendor name: primary exact match after NFC + whitespace normalization.
- Digit accuracy: max(0, 1 − digit_edit_distance / reference_digit_count)
  on the concatenation of annotated digit spans across the document.

A field is "scorable" when the ground truth adjudicated its presence
(PRESENT with a printed value, or ABSENT). UNREADABLE / AMBIGUOUS truth
excludes the field from that denominator but is still reported in
exclusion counts (§7).
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


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

CRITICAL_FIELDS: tuple[str, ...] = (
    "business_tax_id",
    "invoice_number",
    "total_amount",
    "allocation_number",
)

AMOUNT_FIELDS: tuple[str, ...] = (
    "amount_before_vat",
    "vat_amount",
    "total_amount",
)

ID_FIELDS: tuple[str, ...] = (
    "business_tax_id",
    "invoice_number",
    "allocation_number",
)

PRESENCE_PRESENT = "PRESENT"
PRESENCE_ABSENT = "ABSENT"
PRESENCE_UNREADABLE = "UNREADABLE"
PRESENCE_AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class FieldOutcome:
    """One field's scoring outcome for one (document, engine) pair."""

    field: str
    presence: str          # PRESENT | ABSENT | UNREADABLE | AMBIGUOUS
    scorable: bool         # False iff truth is UNREADABLE / AMBIGUOUS
    predicted: Any
    expected: Any
    correct: bool          # only meaningful when scorable
    error_label: str | None = None


@dataclass
class DocumentScore:
    document_id: str
    engine_id: str
    status: str                                  # "ok" | "BLOCKED" | "OCR_FAILURE" | "EXTRACT_FAILURE" | "PAGE_LOAD_ERROR"
    outcomes: list[FieldOutcome] = field(default_factory=list)
    digit_accuracy: float | None = None
    reference_digit_count: int = 0
    critical_complete: bool | None = None        # None if not fully scorable for critical
    all_fields_complete: bool | None = None      # None if not fully scorable for all 8


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def norm_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    s = nfc(value).strip()
    return s if s else None


def norm_vendor(value: Any) -> str | None:
    """Vendor-name normalization: NFC + collapse internal whitespace, strip."""
    base = norm_string(value)
    if base is None:
        return None
    return _WS_RE.sub(" ", base)


def norm_amount(value: Any) -> Decimal | None:
    """Return a Decimal for exact comparison at annotated precision.

    Accepts int/float/str/Decimal. Strings may contain Western digit groupings
    (thousands separator comma / period). We do not fold Arabic-Indic digits
    here — the OCR/parser already resolves digit mapping before serializing.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int,)):
        return Decimal(value)
    if isinstance(value, float):
        # roundtrip through str to avoid float binary artefacts
        return Decimal(repr(value))
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return Decimal(s)
        except InvalidOperation:
            return None
    return None


def norm_date(value: Any) -> str | None:
    """Date normalization: expect an ISO yyyy-mm-dd string."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    s = value.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return None
    return s


# ---------------------------------------------------------------------------
# Per-field scoring
# ---------------------------------------------------------------------------

def _compare_field(field_name: str, predicted: Any, expected: Any) -> bool:
    """Apply the §7 exact-match rule for `field_name`."""
    if field_name == "vendor_name":
        return norm_vendor(predicted) == norm_vendor(expected)
    if field_name in ID_FIELDS:
        return norm_string(predicted) == norm_string(expected)
    if field_name == "invoice_date":
        return norm_date(predicted) == norm_date(expected)
    if field_name in AMOUNT_FIELDS:
        return norm_amount(predicted) == norm_amount(expected)
    # Unknown field — fall back to normalized string equality.
    return norm_string(predicted) == norm_string(expected)


def _presence_from_truth(truth: dict[str, Any], field_name: str) -> tuple[str, Any]:
    """Return (presence_label, expected_value) for one field.

    Looks first at `annotation.field_presence.<field>`; falls back to:
    - value present in ground_truth → PRESENT
    - value explicitly None → ABSENT
    """
    annotation = truth.get("annotation") or {}
    presence_map = annotation.get("field_presence") or {}
    gt = truth.get("ground_truth") or {}
    expected = gt.get(field_name) if isinstance(gt, dict) else None

    presence = presence_map.get(field_name)
    if presence is None:
        presence = PRESENCE_PRESENT if expected is not None else PRESENCE_ABSENT
    return presence, expected


# ---------------------------------------------------------------------------
# Digit accuracy
# ---------------------------------------------------------------------------

_DIGIT_RE = re.compile(r"\d")


def _digits_of(value: Any) -> str:
    if value is None:
        return ""
    return "".join(_DIGIT_RE.findall(str(value)))


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            cur[j] = min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[-1]


def digit_accuracy(predicted_fields: dict[str, Any], expected_fields: dict[str, Any]) -> tuple[float | None, int]:
    """Over the concatenation of annotated numeric fields, compute
    max(0, 1 − digit_edit_distance / reference_digit_count)."""
    pred_digits: list[str] = []
    exp_digits: list[str] = []
    for name in (*ID_FIELDS, "invoice_date", *AMOUNT_FIELDS):
        exp = expected_fields.get(name)
        if exp is None:
            continue
        pred = predicted_fields.get(name)
        pred_digits.append(_digits_of(pred))
        exp_digits.append(_digits_of(exp))
    expected_concat = "".join(exp_digits)
    predicted_concat = "".join(pred_digits)
    ref_count = len(expected_concat)
    if ref_count == 0:
        return None, 0
    dist = _levenshtein(predicted_concat, expected_concat)
    return max(0.0, 1.0 - dist / ref_count), ref_count


# ---------------------------------------------------------------------------
# Entry point — score one (document, engine) row
# ---------------------------------------------------------------------------

def score_row(row: dict[str, Any], truth: dict[str, Any] | None) -> DocumentScore:
    """Score one result row (`results.jsonl` entry) against ground truth.

    Rows with `error` emit a DocumentScore with `status` = error label and
    no outcomes — downstream aggregation will treat them as 0/scorable
    for critical/all-fields complete accuracy (never silently dropped).
    """
    document_id = str(row.get("document_id"))
    engine_id = str(row.get("engine_id"))
    err = row.get("error")
    status = "ok" if err is None else str(err.get("label", "ERROR"))

    score = DocumentScore(document_id=document_id, engine_id=engine_id, status=status)

    if status != "ok" or not truth:
        return score

    prediction = row.get("prediction") or {}
    outcomes: list[FieldOutcome] = []
    expected_values: dict[str, Any] = {}

    for name in BUSINESS_FIELDS:
        presence, expected = _presence_from_truth(truth, name)
        predicted = prediction.get(name)
        if presence in (PRESENCE_UNREADABLE, PRESENCE_AMBIGUOUS):
            outcomes.append(
                FieldOutcome(
                    field=name,
                    presence=presence,
                    scorable=False,
                    predicted=predicted,
                    expected=expected,
                    correct=False,
                )
            )
            continue

        if presence == PRESENCE_ABSENT:
            correct = predicted is None
            label = None if correct else "FIELD_SELECTION_ERROR"
            outcomes.append(
                FieldOutcome(
                    field=name,
                    presence=PRESENCE_ABSENT,
                    scorable=True,
                    predicted=predicted,
                    expected=None,
                    correct=correct,
                    error_label=label,
                )
            )
            continue

        # presence == PRESENT
        expected_values[name] = expected
        correct = _compare_field(name, predicted, expected)
        label = None
        if not correct:
            if predicted is None:
                label = "OCR_DETECTION_ERROR"
            elif name in AMOUNT_FIELDS or name in ID_FIELDS:
                label = "OCR_DIGIT_ERROR"
            else:
                label = "OCR_TEXT_ERROR"
        outcomes.append(
            FieldOutcome(
                field=name,
                presence=PRESENCE_PRESENT,
                scorable=True,
                predicted=predicted,
                expected=expected,
                correct=correct,
                error_label=label,
            )
        )

    score.outcomes = outcomes

    # Critical / all-fields complete flags
    def _complete(fields: Iterable[str]) -> bool | None:
        bucket = [o for o in outcomes if o.field in fields]
        if not bucket:
            return None
        if not all(o.scorable for o in bucket):
            return None
        return all(o.correct for o in bucket)

    score.critical_complete = _complete(CRITICAL_FIELDS)
    score.all_fields_complete = _complete(BUSINESS_FIELDS)

    # Digit accuracy over expected numeric fields present in truth
    acc, ref_count = digit_accuracy(prediction, expected_values)
    score.digit_accuracy = acc
    score.reference_digit_count = ref_count

    return score


# ---------------------------------------------------------------------------
# Aggregation + 95% Wilson intervals
# ---------------------------------------------------------------------------

@dataclass
class EngineAggregate:
    engine_id: str
    submitted: int = 0
    ok: int = 0
    blocked: int = 0
    errored: int = 0
    per_field_present_correct: dict[str, int] = field(default_factory=dict)
    per_field_present_total: dict[str, int] = field(default_factory=dict)
    per_field_absent_correct: dict[str, int] = field(default_factory=dict)
    per_field_absent_total: dict[str, int] = field(default_factory=dict)
    per_field_excluded: dict[str, int] = field(default_factory=dict)
    critical_complete_ok: int = 0
    critical_complete_total: int = 0
    all_fields_complete_ok: int = 0
    all_fields_complete_total: int = 0
    digit_accuracy_sum: float = 0.0
    digit_accuracy_n: int = 0
    error_labels: dict[str, int] = field(default_factory=dict)


def aggregate(scores: Iterable[DocumentScore]) -> dict[str, EngineAggregate]:
    out: dict[str, EngineAggregate] = {}
    for s in scores:
        agg = out.setdefault(s.engine_id, EngineAggregate(engine_id=s.engine_id))
        agg.submitted += 1
        if s.status == "BLOCKED":
            agg.blocked += 1
            continue
        if s.status != "ok":
            agg.errored += 1
            agg.error_labels[s.status] = agg.error_labels.get(s.status, 0) + 1
            continue
        agg.ok += 1
        for o in s.outcomes:
            if not o.scorable:
                agg.per_field_excluded[o.field] = agg.per_field_excluded.get(o.field, 0) + 1
                continue
            if o.presence == PRESENCE_PRESENT:
                agg.per_field_present_total[o.field] = agg.per_field_present_total.get(o.field, 0) + 1
                if o.correct:
                    agg.per_field_present_correct[o.field] = agg.per_field_present_correct.get(o.field, 0) + 1
            else:  # ABSENT
                agg.per_field_absent_total[o.field] = agg.per_field_absent_total.get(o.field, 0) + 1
                if o.correct:
                    agg.per_field_absent_correct[o.field] = agg.per_field_absent_correct.get(o.field, 0) + 1
            if o.error_label:
                agg.error_labels[o.error_label] = agg.error_labels.get(o.error_label, 0) + 1
        if s.critical_complete is not None:
            agg.critical_complete_total += 1
            if s.critical_complete:
                agg.critical_complete_ok += 1
        if s.all_fields_complete is not None:
            agg.all_fields_complete_total += 1
            if s.all_fields_complete:
                agg.all_fields_complete_ok += 1
        if s.digit_accuracy is not None:
            agg.digit_accuracy_sum += s.digit_accuracy
            agg.digit_accuracy_n += 1
    return out


def wilson_interval(successes: int, total: int, confidence: float = 0.95) -> tuple[float, float]:
    """Two-sided Wilson score interval (§7)."""
    if total <= 0:
        return (0.0, 0.0)
    # z for 95% = 1.959963984540054
    z = {0.95: 1.959963984540054, 0.99: 2.5758293035489004}.get(confidence, 1.959963984540054)
    p = successes / total
    denom = 1 + (z**2) / total
    centre = (p + (z**2) / (2 * total)) / denom
    halfw = (z * math.sqrt((p * (1 - p) + (z**2) / (4 * total)) / total)) / denom
    return (max(0.0, centre - halfw), min(1.0, centre + halfw))
