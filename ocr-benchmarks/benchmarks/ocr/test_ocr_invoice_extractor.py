"""Tests for the format-first OCR invoice extractor.

Each test fixture is a verbatim raw-OCR snippet captured from a real
Phase 1 benchmark run (benchmarks/runs/<id>/ocr_text/*.txt), plus the
ground-truth values from benchmarks/data/manifest.phase1.jsonl.

Run:
    cd ocr-benchmarks
    .venv/Scripts/python -m pytest benchmarks/ocr/test_ocr_invoice_extractor.py -v
Or without pytest:
    .venv/Scripts/python benchmarks/ocr/test_ocr_invoice_extractor.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from benchmarks.ocr.ocr_invoice_extractor import extract_from_ocr_text  # noqa: E402


# ---------------------------------------------------------------------------
# photo_2_2 — Mahmoud→Clal invoice
# GT: vendor=כלל חיים, tax=520024647, invoice=90036, date=2026-01-16,
#     subtotal=193.22, vat=34.78, total=228.00
# ---------------------------------------------------------------------------

VISION_PHOTO_2 = """מחמוד שאהין
עוסק מורשה : 302657861
בב חוטה 10 , ירושלים
נייד 052807917
אימייל mahmoudshaheenjerusalem22@gmail.com
חשבונית מס / קבלה 90036
לכבוד : כלל חיים
חפ / ת.ז : 520024647
פירוט שירותים
כמות פירוט
עמלות ביטוח
מחיר ליחידה כן
₪ 193.22
סה"כ
הנחה
מעמ 18 %
סכתשלום
פרטי תשלום
אמצעי תשלום פירוט תאריך
העברה בנקאית
193.22
193.22
( -0.00 )
R134.78
! ₪ 228,00
כרכרט
₪ 28.00 09/01/2026
( -10.00 )
₪ 228,00
הנחה
םהיכ
[ מקור ]
16/01/2026
"""


def test_vision_photo_2_tax_id_prefers_lekavod_party():
    """Should pick 520024647 (לכבוד party) over 302657861 (issuer)."""
    inv = extract_from_ocr_text(VISION_PHOTO_2)
    assert inv.business_tax_id == "520024647"
    assert inv.business_tax_id_valid is True


def test_vision_photo_2_invoice_number():
    inv = extract_from_ocr_text(VISION_PHOTO_2)
    assert inv.invoice_number == "90036"


def test_vision_photo_2_prefers_makor_date_over_payment_date():
    """[מקור] 16/01/2026 is the invoice date; 09/01/2026 is payment date."""
    inv = extract_from_ocr_text(VISION_PHOTO_2)
    assert inv.invoice_date == "2026-01-16"


def test_vision_photo_2_amounts_via_18pct_arithmetic():
    inv = extract_from_ocr_text(VISION_PHOTO_2)
    assert inv.vat_amount == 34.78
    assert inv.amount_before_vat == 193.22
    assert inv.total_amount == 228.00


# ---------------------------------------------------------------------------
# photo_3_1 — Shaheen→Phoenix (10032), via Google Vision
# GT: tax=513000325 (INVALID checksum), invoice=10032, date=2026-02-17,
#     subtotal=23291.70, vat=4192.51, total=27484.21,
#     allocation=20260217113816078161849593
# ---------------------------------------------------------------------------

VISION_PHOTO_3 = """רלנד
הפניקס חברה לביטוח בע"מ
ים עסקדר -830000
מקור
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


def test_vision_photo_3_date_from_tariq_label():
    inv = extract_from_ocr_text(VISION_PHOTO_3)
    assert inv.invoice_date == "2026-02-17"


def test_vision_photo_3_amounts_dot_thousands():
    """23.291.70 (dot-as-thousands) must parse to 23291.70."""
    inv = extract_from_ocr_text(VISION_PHOTO_3)
    assert inv.vat_amount == 4192.51
    assert inv.amount_before_vat == 23291.70
    assert inv.total_amount == 27484.21


def test_vision_photo_3_allocation():
    inv = extract_from_ocr_text(VISION_PHOTO_3)
    assert inv.allocation_number == "20260217713816078161843593"


# ---------------------------------------------------------------------------
# photo_3_1 via Kraken — labels mangled, amounts corrupted
# ---------------------------------------------------------------------------

KRAKEN_PHOTO_3 = """לכ
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


def test_kraken_photo_3_tax_id_still_recovered():
    """Kraken's /וסק חורשה 513000325 — invalid checksum but still extracted."""
    inv = extract_from_ocr_text(KRAKEN_PHOTO_3)
    assert inv.business_tax_id == "513000325"
    # Invalid Israeli checksum (sum=22, mod 10=2).
    assert inv.business_tax_id_valid is False


def test_kraken_photo_3_amounts_not_hallucinated():
    """Kraken's amounts are too mangled (329170, 2329170) — extractor
    should NOT invent values from these corrupted digits. If no 18%
    marker and no clean amounts, prefer null over fake."""
    inv = extract_from_ocr_text(KRAKEN_PHOTO_3)
    # The arithmetic fallback requires a VAT anchor; no 18% in Kraken
    # text on this doc, so amounts should be null.
    assert inv.amount_before_vat is None
    assert inv.vat_amount is None
    assert inv.total_amount is None


# ---------------------------------------------------------------------------
# photo_4 — Shaheen→Migdal (10025), via Tesseract
# GT: tax=520004896, invoice=10025, date=2026-08-10,
#     subtotal=947.00, vat=170.46, total=1117.46
# Tesseract output has labels like "מגדל man לביט", "ana" for סה"כ, etc.
# But the amounts and 18% marker are clean.
# ---------------------------------------------------------------------------

TESSERACT_PHOTO_4 = """שאהין מחמוד
לכבוד
0
מגדל man‏ לביט
/.9.n‏ עוסק מורשה 520004896
חשבונית מס 0025
mora עמלות‎
מחיר לפני הנחה.
947.00
0.00
ana
947.00
sae
מע"מ
)18.00%( 170.46
סה"כ לתעולום
1,117.46 nw‏
"""


def test_tesseract_photo_4_tax_id():
    inv = extract_from_ocr_text(TESSERACT_PHOTO_4)
    assert inv.business_tax_id == "520004896"
    assert inv.business_tax_id_valid is True


def test_tesseract_photo_4_invoice_number_misread():
    """Tesseract OCR'd invoice '10025' as '0025' — extractor must return
    whatever is in the text, not try to be clever and guess."""
    inv = extract_from_ocr_text(TESSERACT_PHOTO_4)
    assert inv.invoice_number == "0025"


def test_tesseract_photo_4_amounts():
    inv = extract_from_ocr_text(TESSERACT_PHOTO_4)
    assert inv.vat_amount == 170.46
    assert inv.amount_before_vat == 947.00
    assert inv.total_amount == 1117.46


# ---------------------------------------------------------------------------
# photo_4 via Google Vision — everything clean
# ---------------------------------------------------------------------------

VISION_PHOTO_4 = """CARMEL
לכבוד
מגדל חברה לביט
ח.פ. / עוסק מורשה 520004896
פריט
עמלות ביטוח
מחיר לפני הנחה
הנחה
סה"כ
מע"מ
סה"כ לתשלום
חשבונית מס 10025
שאהין מחמוד
ירושליים
מס ' טלפון 0528073917
מחיר למות הנחה סכום
947.00 0,00 1.00 947.00
( -HD )
302657861
תאריך
10-08-2026
947.00
0.00
947.00
170.46 ( 18.00 % )
מקור
1,117.46 שייח
"""


def test_vision_photo_4_full_critical_set():
    inv = extract_from_ocr_text(VISION_PHOTO_4)
    assert inv.business_tax_id == "520004896"
    assert inv.invoice_number == "10025"
    assert inv.invoice_date == "2026-08-10"
    assert inv.vat_amount == 170.46
    assert inv.amount_before_vat == 947.00
    assert inv.total_amount == 1117.46


def test_vision_photo_4_vendor_name_near_lekavod():
    inv = extract_from_ocr_text(VISION_PHOTO_4)
    # Should pick "מגדל חברה לביט" (the לכבוד party, truncated as printed).
    assert inv.vendor_name and "מגדל" in inv.vendor_name


# ---------------------------------------------------------------------------
# Noise rejection: null input, empty, garbage
# ---------------------------------------------------------------------------

def test_empty_input():
    inv = extract_from_ocr_text("")
    assert inv.business_tax_id is None
    assert inv.invoice_number is None
    assert inv.amount_before_vat is None


def test_garbage_only_no_hallucination():
    """Pure garbage should produce an empty invoice, not invented values."""
    inv = extract_from_ocr_text("gibberish nothing here just text")
    assert inv.business_tax_id is None
    assert inv.invoice_number is None
    assert inv.invoice_date is None
    assert inv.amount_before_vat is None
    assert inv.vat_amount is None
    assert inv.total_amount is None
    assert inv.allocation_number is None


# ---------------------------------------------------------------------------
# Non-regression: handwritten receipt expected to score low across the board
# ---------------------------------------------------------------------------

TESSERACT_HANDWRITTEN = """Lay 5‏ ג. ,| 414 א--ץ23 (becall‏
- ב שא ופט ו 0
2314 dist / Mayes] abn Je
חשבונית מס / קבלה‎ 513850073 ‘an
"""


def test_handwritten_gets_at_least_business_number():
    """Handwritten receipt should at least recover the printed form number."""
    inv = extract_from_ocr_text(TESSERACT_HANDWRITTEN)
    # 513850073 is on the printed form; checksum is valid.
    assert inv.business_tax_id == "513850073"
    assert inv.business_tax_id_valid is True


# ---------------------------------------------------------------------------
# Runner (no pytest fallback)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import traceback
    tests = [name for name in globals() if name.startswith("test_")]
    failures: list[tuple[str, str]] = []
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
