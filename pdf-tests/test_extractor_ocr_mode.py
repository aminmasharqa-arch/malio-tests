"""Tests for the ocr_tolerant extraction path.

Each test case is a verbatim raw-OCR snippet captured from the Phase 1
benchmark (benchmarks/runs/<id>/ocr_text/<doc>__<engine>.txt), plus the
canonical ground-truth values the ocr_tolerant parser should recover.

Run:
    cd pdf-tests
    python -m pytest test_extractor_ocr_mode.py -v
Or without pytest:
    python test_extractor_ocr_mode.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extractor import (  # noqa: E402
    IsraeliInvoice,
    _to_float,
    _to_float_ocr,
    extract_from_text,
    fuzzy_normalize_labels,
)


# --------------------------------------------------------------------------
# Number parser
# --------------------------------------------------------------------------

def test_to_float_ocr_western_comma_thousands():
    # standard "23,291.70" from text-layer PDFs or clean OCR
    assert _to_float_ocr("23,291.70") == 23291.70
    assert _to_float_ocr("1,117.46") == 1117.46


def test_to_float_ocr_dot_thousands():
    # Google Vision's "23.291.70" (dot-as-thousands, dot-as-decimal)
    assert _to_float_ocr("23.291.70") == 23291.70
    assert _to_float_ocr("4.192.51") == 4192.51
    assert _to_float_ocr("27.484.21") == 27484.21


def test_to_float_ocr_plain_decimal():
    assert _to_float_ocr("193.22") == 193.22
    assert _to_float_ocr("947.00") == 947.00
    assert _to_float_ocr("947") == 947.0


def test_to_float_ocr_european_comma_decimal():
    # Some engines emit "193,22" with comma as decimal.
    assert _to_float_ocr("193,22") == 193.22


def test_to_float_ocr_handles_currency_and_spaces():
    assert _to_float_ocr("₪ 193.22") == 193.22
    assert _to_float_ocr("228.00 ") == 228.00


def test_to_float_ocr_handles_none_and_junk():
    assert _to_float_ocr(None) is None
    assert _to_float_ocr("") is None
    assert _to_float_ocr("nu") is None


def test_to_float_strict_still_rejects_dot_thousands():
    # The strict parser used for text-layer PDFs must NOT silently
    # misread Vision's dot-thousands format — that's the point of
    # keeping two parsers.
    assert _to_float("23.291.70") is None


# --------------------------------------------------------------------------
# Fuzzy label normalization
# --------------------------------------------------------------------------

def test_fuzzy_normalizes_vat_label():
    # Kraken on photo_3_1 produced the mangled VAT label (ח for מ).
    txt = "חמ\"מ 4.192.51"
    out = fuzzy_normalize_labels(txt)
    assert "מע\"מ" in out


def test_fuzzy_normalizes_subtotal_label():
    # "סהב לתשחם" (Kraken) → "סה\"כ לתשלום"
    txt = "סהב לתשחם 27.484.21"
    out = fuzzy_normalize_labels(txt)
    assert "סה\"כ" in out
    assert "לתשלום" in out


def test_fuzzy_normalizes_osek_morshe_label():
    # Kraken's "/וסק חורשה" → canonical עוסק מורשה
    txt = "/וסק חורשה 513000325"
    out = fuzzy_normalize_labels(txt)
    assert "עוסק מורשה" in out
    assert "513000325" in out  # the number itself must survive


def test_fuzzy_normalizes_price_before_discount():
    txt = "מתיר לפני הנחה"  # Kraken
    assert "מחיר לפני הנחה" in fuzzy_normalize_labels(txt)
    txt2 = "חזיר לפני הנחה"  # Vision
    assert "מחיר לפני הנחה" in fuzzy_normalize_labels(txt2)


def test_fuzzy_is_idempotent():
    txt = "מע\"מ 4,192.51 סה\"כ 23,291.70"
    assert fuzzy_normalize_labels(fuzzy_normalize_labels(txt)) == fuzzy_normalize_labels(txt)


# --------------------------------------------------------------------------
# End-to-end on real OCR captures from benchmarks/runs/
# --------------------------------------------------------------------------

# Verbatim raw OCR text from
# benchmarks/runs/phase1-20261007T193356Z/ocr_text/photo_3_1__kraken-medium-cpu.txt
KRAKEN_PHOTO_3_RAW = """לכ
הפניקם חברה לביסוח בעים
/וסק חורשה 513000325
ת
שמלות ביטום
שאהין תחמה
רים
305E
1702 2026
מתיר לפני הנחה
נ
סהב לתשחם
329170
2329170
"""


def test_kraken_photo_3_recovers_tax_id():
    inv = extract_from_text(
        KRAKEN_PHOTO_3_RAW,
        reverse_rtl_tokens=False,
        fix_pymupdf_abbreviations=False,
        ocr_tolerant=True,
    )
    # After fuzzy normalization, "/וסק חורשה 513000325" becomes
    # "עוסק מורשה 513000325" — business_tax_id regex should hit.
    assert inv.business_tax_id == "513000325"


# Verbatim raw OCR text from the Google Vision probe on photo_3_1.
VISION_PHOTO_3_RAW = """רלנד
הפניקס חברה לביטוח בע"מ
ים עסקדר -830000
מקור
MOJ NÚDU
חזיר לפני הנחה .
שכלתשלום
חשבונית מסנן
20260217713816078161843593 x 1on
שארין מחמוד
05480139 137 00
30265785
תאריך
17-02-2026
23.291.70
0.00
23.291.70
4.192.51 ( 18.00 % )
27.484.21 nu
"""


def test_vision_photo_3_recovers_amounts():
    inv = extract_from_text(
        VISION_PHOTO_3_RAW,
        reverse_rtl_tokens=False,
        fix_pymupdf_abbreviations=False,
        ocr_tolerant=True,
    )
    # The three amounts are present in dot-thousands format in Vision's
    # raw OCR; the ocr_tolerant parser should recover all three via
    # the 18% positional VAT + arithmetic fallback.
    assert inv.vat_amount == 4192.51
    assert inv.amount_before_vat == 23291.70
    assert inv.total_amount == 27484.21


def test_vision_photo_3_recovers_date():
    inv = extract_from_text(
        VISION_PHOTO_3_RAW,
        reverse_rtl_tokens=False,
        fix_pymupdf_abbreviations=False,
        ocr_tolerant=True,
    )
    assert inv.invoice_date == "2026-02-17"


# --------------------------------------------------------------------------
# Allocation-number positional fallback
# --------------------------------------------------------------------------

def test_allocation_long_digit_positional():
    # Long 26-digit allocation on a line of its own, no label.
    txt = """שלום
20260217113816078161849593 x 1on
תאריך 17-02-2026
"""
    inv = extract_from_text(
        txt,
        reverse_rtl_tokens=False,
        fix_pymupdf_abbreviations=False,
        ocr_tolerant=True,
    )
    assert inv.allocation_number == "20260217113816078161849593"


def test_allocation_not_captured_from_short_number_in_strict_mode():
    # Strict mode must NOT grab the number as allocation (there's no label).
    txt = "20260217113816078161849593"
    inv = extract_from_text(
        txt,
        reverse_rtl_tokens=False,
        fix_pymupdf_abbreviations=False,
        ocr_tolerant=False,
    )
    assert inv.allocation_number is None


# --------------------------------------------------------------------------
# Non-regression: text-layer PDF behavior unchanged
# --------------------------------------------------------------------------

def test_strict_mode_unchanged_on_clean_hebrew():
    # Clean Hebrew with proper labels + Western number format should
    # work in BOTH modes identically — this guards against the fuzzy
    # layer accidentally damaging clean text.
    txt = """לכבוד
כלל חיים
ח.פ/ת.ז: 520024647
חשבונית מס/קבלה 90036
תאריך 16/01/2026
סה"כ 193.22
מע"מ 34.78
סה"כ לתשלום 228.00
"""
    inv_strict = extract_from_text(txt)
    inv_tolerant = extract_from_text(txt, ocr_tolerant=True)
    for field in ("business_tax_id", "invoice_number", "invoice_date",
                   "amount_before_vat", "vat_amount", "total_amount"):
        assert getattr(inv_strict, field) == getattr(inv_tolerant, field), (
            f"{field} differs: strict={getattr(inv_strict, field)!r} "
            f"tolerant={getattr(inv_tolerant, field)!r}"
        )


# --------------------------------------------------------------------------
# Runner (no-pytest fallback)
# --------------------------------------------------------------------------

if __name__ == "__main__":
    import traceback
    failures: list[tuple[str, str]] = []
    tests = [name for name in globals() if name.startswith("test_")]
    for name in tests:
        try:
            globals()[name]()
            print(f"  PASS  {name}")
        except Exception:
            failures.append((name, traceback.format_exc()))
            print(f"  FAIL  {name}")
    print()
    print(f"{len(tests) - len(failures)}/{len(tests)} tests passed")
    for name, tb in failures:
        print(f"\n--- {name} ---\n{tb}")
    sys.exit(1 if failures else 0)
