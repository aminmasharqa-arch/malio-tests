"""
Deterministic Israeli-invoice field extractor.

Pure Python, no AI/LLM calls at runtime. Given a well-formed Hebrew
invoice PDF, returns a structured JSON matching the Israeli invoice
schema:
    - vendor_name            (שם העסק / הספק)
    - business_tax_id        (ח.פ. / ע.מ. / מספר עוסק  — 9 digits)
    - business_tax_id_valid  (checksum validity)
    - invoice_number         (מספר חשבונית)
    - invoice_date           (תאריך — ISO yyyy-mm-dd)
    - amount_before_vat      (סה"כ לפני מע"מ)
    - vat_amount             (סכום המע"מ)
    - total_amount           (סה"כ כולל מע"מ / לתשלום)
    - allocation_number      (מספר הקצאה — Israel Tax Authority 2024+)

Pipeline:
    1. Extract text layer with PyMuPDF (deterministic for well-formed PDFs).
    2. Hebrew normalization — adapted from the `hebrew-ocr-forms` skill:
       - strip bidi control characters (Unicode Cf)
       - strip nikud (U+0591..U+05C7)
       - fold final-form (sofit) letters for matching (keys only)
    3. PyMuPDF-specific fixes (NOT in the skill, needed for text-layer PDFs):
       - reverse whitespace-separated token order per Hebrew line
       - swap known abbreviation reversals (כ"סה→סה"כ, מ"מע→מע"מ, ח"ש→ש"ח)
    4. Label-anchored regex extraction with Israeli-ID checksum validation.

Usage:
    python extractor.py invoice.pdf                 -> JSON to stdout
    python extractor.py folder/                     -> JSON array to stdout
    python extractor.py folder/ --out results.json  -> JSON array to file

As a library:
    from extractor import extract_from_pdf
    invoice = extract_from_pdf("invoice.pdf")
    print(invoice.to_json())
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

# PyMuPDF is only needed for text-layer extraction in `extract_text`. Import
# lazily so OCR consumers can call `extract_from_text` without PyMuPDF installed.


# =============================================================================
# SCHEMA
# =============================================================================


@dataclass
class IsraeliInvoice:
    source_file: Optional[str] = None
    vendor_name: Optional[str] = None
    business_tax_id: Optional[str] = None
    business_tax_id_valid: Optional[bool] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    amount_before_vat: Optional[float] = None
    vat_amount: Optional[float] = None
    total_amount: Optional[float] = None
    allocation_number: Optional[str] = None
    extraction_notes: list[str] = field(default_factory=list)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=indent)


# =============================================================================
# HEBREW NORMALIZATION
# Utilities adapted from the hebrew-ocr-forms skill, plus two PyMuPDF-specific
# fixes the skill does not cover (token reversal + quoted-abbreviation swap).
# =============================================================================


HEBREW_RE = re.compile(r"[֐-׿]")
NIKUD_RE = re.compile(r"[֑-ׇ]")

SOFIT_MAP = {"ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ"}

# Hebrew abbreviations with an internal double-quote are often emitted by
# PyMuPDF with their characters reversed (because the quote splits the BiDi
# run). Map wrong-form -> correct form.
ABBREV_FIX = {
    "כ\"סה": "סה\"כ",   # total
    "מ\"מע": "מע\"מ",   # VAT
    "ח\"ש": "ש\"ח",     # NIS
    "מ\"בע": "בע\"מ",   # Ltd (company suffix)
    # Hebrew gershayim (U+05F4) variants
    "כ״סה": "סה״כ",
    "מ״מע": "מע״מ",
    "ח״ש": "ש״ח",
    "מ״בע": "בע״מ",
}


def strip_bidi_controls(text: str) -> str:
    """Strip Unicode format characters (category Cf) — bidi marks etc."""
    return "".join(c for c in text if unicodedata.category(c) != "Cf")


def strip_nikud(text: str) -> str:
    """Remove Hebrew diacritics so keyword regexes match reliably."""
    return NIKUD_RE.sub("", text)


def fold_sofit(text: str) -> str:
    """Fold final-form Hebrew letters to their medial forms (for matching)."""
    return "".join(SOFIT_MAP.get(ch, ch) for ch in text)


def fix_quoted_abbreviations(text: str) -> str:
    for wrong, right in ABBREV_FIX.items():
        text = text.replace(wrong, right)
    return text


def reverse_tokens_per_hebrew_line(text: str) -> str:
    """
    PyMuPDF emits Hebrew lines with word order reversed (because glyphs are
    laid out RTL on the page but extracted LTR). Reverse whitespace-separated
    tokens on Hebrew-containing lines; leave LTR-only lines untouched.
    """
    out: list[str] = []
    for line in text.splitlines():
        if HEBREW_RE.search(line):
            out.append(" ".join(reversed(line.split())))
        else:
            out.append(line)
    return "\n".join(out)


def normalize_for_matching(
    raw: str,
    *,
    reverse_rtl_tokens: bool = True,
    fix_pymupdf_abbreviations: bool = True,
) -> str:
    """Full normalization: bidi strip → (abbrev fix) → (token reverse) → nikud strip.

    The abbreviation fix and token reversal are PyMuPDF-specific repairs:
    PyMuPDF emits Hebrew words in visual (not reading) order and splits
    quoted abbreviations across the quote. OCR engines like Tesseract emit
    words in reading order already, so disable both flags for OCR input.
    """
    text = strip_bidi_controls(raw)
    if fix_pymupdf_abbreviations:
        text = fix_quoted_abbreviations(text)
    if reverse_rtl_tokens:
        text = reverse_tokens_per_hebrew_line(text)
    text = strip_nikud(text)
    return text


# =============================================================================
# VALIDATION
# =============================================================================


def validate_israeli_id(value: str) -> bool:
    """9-digit Israeli ID / Tax ID checksum (official algorithm)."""
    if not value or not value.isdigit() or len(value) != 9:
        return False
    total = 0
    for i, ch in enumerate(value):
        v = int(ch) * (1 if i % 2 == 0 else 2)
        if v > 9:
            v -= 9
        total += v
    return total % 10 == 0


# =============================================================================
# PARSING HELPERS
# =============================================================================


NUMBER_RE = r"[-+]?\d{1,3}(?:[, ]\d{3})*(?:\.\d+)?|[-+]?\d+(?:\.\d+)?"

# OCR-tolerant number regex. Accepts three real-world formats seen in our
# benchmark captures:
#   - Western with comma-thousands + dot-decimal: "23,291.70"
#   - European-style / Google Vision dot-thousands:     "23.291.70"
#   - Plain numeric:                                     "193.22", "947"
# Semantics (resolved in _to_float_ocr): if a number contains ≥2 '.' OR a
# single '.' with exactly 3 digits after and no '.NN' decimal part, the
# dots are thousands separators. Otherwise the last '.' is the decimal.
#
# No sign prefix (otherwise `17-02-2026` would be read as 17, -02, -2026).
# Thousands separator is literal space, comma, or dot — NOT \s, so it does
# not grab across line breaks.
NUMBER_RE_OCR = (
    r"\d{1,3}(?:[,. ]\d{3})+(?:[.,]\d{1,2})?"  # thousands groups + optional decimal
    r"|\d+[.,]\d{1,2}"                            # single decimal
    r"|\d+"                                        # plain integer
)


def _to_float(raw: Optional[str]) -> Optional[float]:
    if raw is None:
        return None
    cleaned = (
        raw.replace(" ", "")
        .replace(" ", "")  # NBSP
        .replace(",", "")
        .replace("₪", "")
        .strip()
    )
    try:
        return float(cleaned)
    except ValueError:
        return None


def _to_float_ocr(raw: Optional[str]) -> Optional[float]:
    """Parse amounts from OCR text with multiple separator conventions.

    Rules (applied in order):
    - Strip currency and whitespace.
    - If both '.' and ',' are present: whichever appears LAST is the decimal.
    - If only ',' is present and it has exactly 1-2 digits after → decimal.
    - If only ',' is present and 3 digits after → thousands separator.
    - If only '.' is present and it has 1-2 digits after → decimal.
    - If only '.' is present and 3 digits after → thousands separator.
    - Multiple same separators → all treated as thousands except possibly
      the LAST if it has 1-2 trailing digits (Vision's 23.291.70 pattern).
    """
    if raw is None:
        return None
    s = raw.strip()
    for ch in (" ", " ", "₪", "$", "€"):
        s = s.replace(ch, "")
    s = s.lstrip("+")
    if not s:
        return None

    has_comma = "," in s
    has_dot = "." in s

    if has_comma and has_dot:
        if s.rfind(".") > s.rfind(","):
            # Western: commas are thousands, last dot is decimal.
            s = s.replace(",", "")
        else:
            # European: dots are thousands, last comma is decimal.
            s = s.replace(".", "").replace(",", ".", 1).replace(",", "")
    elif has_dot:
        parts = s.split(".")
        if len(parts) > 2:
            # 23.291.70 → last part is decimal if 1-2 digits, else all thousands.
            if 1 <= len(parts[-1]) <= 2:
                s = "".join(parts[:-1]) + "." + parts[-1]
            else:
                s = "".join(parts)
        # len(parts) == 2 → single '.', treat as decimal (standard).
    elif has_comma:
        parts = s.split(",")
        if len(parts) > 2:
            if 1 <= len(parts[-1]) <= 2:
                s = "".join(parts[:-1]) + "." + parts[-1]
            else:
                s = "".join(parts)
        else:
            # Single comma: 1-2 digits after → decimal, else thousands.
            if 1 <= len(parts[-1]) <= 2:
                s = parts[0] + "." + parts[-1]
            else:
                s = "".join(parts)
    try:
        return float(s)
    except ValueError:
        return None


# =============================================================================
# OCR-TOLERANT LABEL NORMALIZATION
# =============================================================================
# OCR engines mangle Hebrew labels in predictable ways (ח↔מ, ם↔ס, ת↔ח,
# ו↔י↔ן, ע↔/). We can't make the parser tolerant to arbitrary noise, but
# we CAN canonicalize a small set of known labels (עוסק מורשה, מע"מ, סה"כ,
# מספר הקצאה, מחיר לפני הנחה, לתשלום) so the existing regex extractors
# find them. We only touch label text — numbers remain untouched.
#
# Patterns were derived from actual engine output in benchmarks/runs/ and
# probes/runs/. If you spot a new corruption, add it here with a comment
# noting which engine × document first produced it.

_FUZZY_LABEL_SUBS: tuple[tuple[str, str], ...] = (
    # עוסק מורשה — tax-ID label on both issuer and customer sides.
    # Variants seen: "/וסק חורשה" (Kraken), "עסקדר" (Vision — compressed),
    # "pסוע השרומ" (Tesseract — Latin p for ע, reversed), "עוסק חורשה",
    # "עוסק מורש[החה]".
    (r"[עצ/\\p][ווי]?\s*ס[קכ]\s*[חמה][וויר]+[רד]?\s*ש[החה]", "עוסק מורשה"),
    # מע"מ — VAT label. Variants: "חמ\"מ" (Kraken ח↔מ), "מע'מ", "חמ מ",
    # "מ\"עמ", "מעם" (sofit swap), "מעמ". The first two chars allow full
    # ח↔מ↔ע swapping because engines confuse all three stems; a quote
    # mark + trailing [מם] is required to anchor the match (prevents
    # matching inside unrelated 3-letter Hebrew words).
    (r"[מחע][מחע][\"״׳'`][מם]", "מע\"מ"),
    # סה"כ — subtotal/total label. Variants: "סהב", "סהכ", "שה\"כ",
    # "שכ" (truncated), "סה״כ". Letters MUST be adjacent (no spaces) and
    # the trailing [בכ] is a hard anchor to avoid matching across unrelated
    # Hebrew words like "הפניקס חברה".
    (r"[סש][החה][\"״׳'`]?[בכ](?![א-ת])", "סה\"כ"),
    # לתשלום — "to be paid". Variants: "לתשחם" (Kraken), "שכלתשלום"
    # (Vision glues סה\"כ + לתשלום), "כלתשלום". Letters must be adjacent.
    (r"[לכ][תפ]ש[חל][וויי]?[מם]", "לתשלום"),
    # מחיר לפני הנחה — subtotal label. Variants: "מתיר לפני הנחה" (Kraken ת↔ח),
    # "חזיר לפני הנחה" (Vision ח↔מ, ז↔ח).
    (r"[מח][חתז][זיי][רד]\s*לפני\s*[החה]נ[חה][החה]", "מחיר לפני הנחה"),
    # מספר הקצאה — allocation-number label. Variants: "חספר הקצאה",
    # "מספר הצצאה".
    (r"[מח]ס[פפ]\s*[רד]?\s*[החה][קכ][צך][אה][חה]", "מספר הקצאה"),
    # חשבונית מס — invoice label. Variants: "חשבונית מסנן" (Vision adds
    # noise), "תינובשח" (reversed), "חשבוננית".
    (r"[חת]ש\s*[בכ][וויי][נן]+[יוי]?[תחה]\s*[מם][סש]?", "חשבונית מס"),
)

_FUZZY_LABEL_REGEX: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p), repl) for p, repl in _FUZZY_LABEL_SUBS
)


def fuzzy_normalize_labels(text: str) -> str:
    """Rewrite common OCR-mangled Hebrew labels to their canonical forms.

    Only labels are substituted — the surrounding tokens (numbers, vendor
    names, dates) are preserved exactly. Idempotent: running twice is a
    no-op.
    """
    for pattern, repl in _FUZZY_LABEL_REGEX:
        text = pattern.sub(repl, text)
    return text


def _normalize_date(raw: str) -> Optional[str]:
    """Normalize dd/mm/yyyy, dd-mm-yyyy, dd.mm.yyyy → ISO yyyy-mm-dd."""
    m = re.search(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4}|\d{2})", raw)
    if not m:
        return None
    day, month, year = m.groups()
    if len(year) == 2:
        year = "20" + year
    try:
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    except ValueError:
        return None


def _find_first(pattern: str, text: str, flags: int = 0) -> Optional[str]:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


# =============================================================================
# FIELD EXTRACTORS
# Each extractor runs against the *normalized* text and returns Optional[str/float].
# =============================================================================


def _extract_business_tax_id(text: str, invoice: IsraeliInvoice) -> None:
    # Vendor-specific labels (strongest signal — this is the issuer's ID).
    vendor_label = r"(?:עוסק\s*מורשה|מספר\s*עוסק)"
    # Generic labels that may apply to either party.
    generic_label = (
        r"(?:ח\.?\s*פ\.?|ע\.?\s*מ\.?|ת\.?\s*ז\.?/ח\.?\s*פ\.?|"
        r"ח\.?\s*פ\.?\s*/\s*עוסק\s*מורשה)"
    )

    def _collect(label: str) -> list[str]:
        out: list[str] = []
        out += re.findall(rf"{label}[^\d]{{0,20}}(\d{{9}})", text)
        out += re.findall(rf"(\d{{9}})[^\d]{{0,20}}{label}", text)
        return out

    vendor_labelled = _collect(vendor_label)
    generic_labelled = _collect(generic_label)

    chosen: Optional[str] = None

    # Priority order:
    #   1. Vendor-labelled + checksum valid
    #   2. Vendor-labelled (even if checksum fails — synthetic test IDs)
    #   3. Generic-labelled + checksum valid
    #   4. Any 9-digit with valid checksum
    #   5. Generic-labelled (last resort)
    chosen = next((c for c in vendor_labelled if validate_israeli_id(c)), None)

    if not chosen and vendor_labelled:
        chosen = vendor_labelled[0]
        if not validate_israeli_id(chosen):
            invoice.extraction_notes.append(
                "business_tax_id (עוסק מורשה) has invalid checksum"
            )

    if not chosen:
        chosen = next(
            (c for c in generic_labelled if validate_israeli_id(c)), None
        )

    if not chosen:
        for cand in re.findall(r"(?<!\d)(\d{9})(?!\d)", text):
            if validate_israeli_id(cand):
                chosen = cand
                invoice.extraction_notes.append(
                    "business_tax_id matched by checksum, not label"
                )
                break

    if not chosen and generic_labelled:
        chosen = generic_labelled[0]
        invoice.extraction_notes.append(
            "business_tax_id has invalid checksum (no valid alternative found)"
        )

    invoice.business_tax_id = chosen
    if chosen:
        invoice.business_tax_id_valid = validate_israeli_id(chosen)


def _extract_invoice_number(text: str, invoice: IsraeliInvoice) -> None:
    invoice.invoice_number = _find_first(
        r"(?:חשבונית(?:\s*מס)?(?:/קבלה)?(?:\[מקור\])?|"
        r"מספר\s*חשבונית|חש[\"״']?\s*מס)"
        r"[^\d]{0,20}(\d{3,})",
        text,
    )


def _extract_invoice_date(text: str, invoice: IsraeliInvoice) -> None:
    # Payment-method line typically holds the real transaction date on
    # Israeli receipts; a bare top-of-page date is usually the export date.
    chosen = _find_first(
        r"(?:העברה\s*בנקאית|תשלום|מזומן|אשראי|שיק)"
        r"[^\d]{0,10}(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
        text,
    ) or _find_first(
        r"תאריך[^\d]{0,20}(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
        text,
    )

    if chosen:
        invoice.invoice_date = _normalize_date(chosen)
        return

    any_date = _find_first(r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})", text)
    if any_date:
        invoice.invoice_date = _normalize_date(any_date)
        invoice.extraction_notes.append(
            "invoice_date matched without payment/תאריך anchor"
        )


def _extract_amounts_impl(
    text: str,
    invoice: IsraeliInvoice,
    *,
    number_re: str,
    to_float,  # callable: str -> Optional[float]
    enable_positional_fallbacks: bool = False,
) -> None:
    # ---- Grand total ---------------------------------------------------
    # Label-after-number variants (vendor "A" layout) and after-number
    # variants (vendor "B" layout: "462.52סה\"כ אחרי מע\"מ").
    total = _find_first(
        rf"סה[\"״']?\s*כ[^\d\n]{{0,10}}({number_re})[^\n]*לתשלום",
        text,
    ) or _find_first(
        rf"(?:לתשלום\s*סה[\"״']?\s*כ|"
        rf"סה[\"״']?\s*כ\s*(?:כולל|אחרי)\s*מע[\"״']?\s*מ|לתשלום)"
        rf"[^\d\-+\n]{{0,15}}({number_re})",
        text,
    ) or _find_first(
        rf"({number_re})[^\d\-+\n]{{0,5}}סה[\"״']?\s*כ\s*"
        rf"(?:אחרי|כולל)\s*מע[\"״']?\s*מ",
        text,
    ) or _find_first(
        # Variant: "{אחרי מע\"מ|מע\"מ אחרי} ... {number} ... סה\"כ"
        # (labels on left, number glued to סה"כ on the right)
        rf"(?:אחרי\s*מע[\"״']?\s*מ|מע[\"״']?\s*מ\s*אחרי)"
        rf"[^\d\n]*({number_re})[^\d\n]*סה[\"״']?\s*כ",
        text,
    )

    # ---- VAT amount ----------------------------------------------------
    # Preferred: a number IMMEDIATELY adjacent to "מע\"מ" (either side,
    # with at most a currency symbol or whitespace between). The old
    # "\s*\d{1,2}" variants greedily ate the leading digit of amounts
    # like "8,757.30" when stuck to the label.
    vat = _find_first(
        rf"מע[\"״׳']?\s*מ[\s₪$]{{0,5}}({number_re})",
        text,
    ) or _find_first(
        rf"({number_re})[\s₪$]{{0,5}}מע[\"״׳']?\s*מ"
        rf"(?!\s*לפני)(?!\s*אחרי)",
        text,
    )

    # ---- Pre-VAT subtotal ---------------------------------------------
    # Variant A: "סה\"כ ...number" line that is NOT the total line.
    subtotal: Optional[str] = None
    for m in re.finditer(
        rf"סה[\"״']?\s*כ(?!\s*לתשלום)(?!\s*(?:כולל|אחרי))"
        rf"[^\d\-+\n]{{0,10}}({number_re})(?![^\n]*לתשלום)",
        text,
    ):
        subtotal = m.group(1)
        break

    # Variant B: "number...סה\"כ לפני מע\"מ" (number before full phrase).
    if subtotal is None:
        subtotal = _find_first(
            rf"({number_re})[^\d\-+\n]{{0,5}}סה[\"״']?\s*כ\s*לפני\s*מע[\"״']?\s*מ",
            text,
        )

    # Variant C: "{לפני מע\"מ|מע\"מ לפני} ... {number} ... סה\"כ"
    # (template with the label on the left and the number glued to סה"כ)
    if subtotal is None:
        subtotal = _find_first(
            rf"(?:לפני\s*מע[\"״']?\s*מ|מע[\"״']?\s*מ\s*לפני)"
            rf"[^\d\n]*({number_re})[^\d\n]*סה[\"״']?\s*כ",
            text,
        )

    # ---- Positional VAT fallback (OCR mode only) ----------------------
    # When Hebrew labels are too mangled for the regexes above but we can
    # still see "18%" / "(18.00%)" next to a number, that number is VAT.
    # Then subtotal/total are inferred by arithmetic from the surrounding
    # numbers (A + VAT ≈ B).
    if enable_positional_fallbacks and vat is None:
        # Look for `NUMBER ( 18.00 % )` or `NUMBER 18%` within a short span.
        m = re.search(
            rf"({number_re})\s*\(?\s*18(?:[.,]\d{{1,2}})?\s*%\s*\)?",
            text,
        )
        if m:
            vat = m.group(1)
            invoice.extraction_notes.append(
                "vat_amount via positional 18%% fallback"
            )

    v_amount = to_float(subtotal)
    v_vat = to_float(vat)
    v_total = to_float(total)

    # Positional subtotal/total: if we have VAT (from the 18% match above
    # or from a labeled match), infer the missing subtotal/total.
    # Strategy: VAT at 18% implies subtotal = VAT / 0.18 and
    # total = subtotal + VAT. We then look for ACTUAL numbers in the
    # text that match these expected values (within 1.0 for rounding).
    # This is far more robust than enumerating all pairs, which lets
    # giant 20+ digit allocation numbers hijack the result (A + VAT ≈ A).
    if enable_positional_fallbacks and v_vat is not None:
        if v_amount is None or v_total is None:
            # Only accept VATs that look like real invoice VAT (not
            # giant allocation-number-sized values).
            if 0 < v_vat < 1_000_000:
                expected_subtotal = v_vat / 0.18
                expected_total = expected_subtotal + v_vat

                def _find_near(expected: float) -> Optional[float]:
                    best: Optional[tuple[float, float]] = None  # (|diff|, value)
                    for m in re.finditer(number_re, text):
                        try:
                            val = to_float(m.group(0))
                        except Exception:
                            continue
                        if val is None or not (0 < val < 10_000_000):
                            continue
                        diff = abs(val - expected)
                        # Within 1.0 absolute OR 0.5% relative (whichever
                        # is larger) to tolerate OCR rounding like
                        # "₪34.78" when the exact VAT would be 34.7796.
                        tol = max(1.0, expected * 0.005)
                        if diff <= tol and (best is None or diff < best[0]):
                            best = (diff, val)
                    return best[1] if best else None

                found_subtotal = _find_near(expected_subtotal)
                found_total = _find_near(expected_total)
                if found_subtotal is not None and v_amount is None:
                    v_amount = found_subtotal
                if found_total is not None and v_total is None:
                    v_total = found_total
                if found_subtotal is not None or found_total is not None:
                    invoice.extraction_notes.append(
                        "amount_before_vat / total via 18% arithmetic fallback"
                    )

    invoice.amount_before_vat = v_amount
    invoice.vat_amount = v_vat
    invoice.total_amount = v_total

    if (
        invoice.amount_before_vat is not None
        and invoice.vat_amount is not None
        and invoice.total_amount is not None
    ):
        if abs(
            invoice.amount_before_vat + invoice.vat_amount - invoice.total_amount
        ) > 1.0:
            invoice.extraction_notes.append(
                f"amount check mismatch: "
                f"{invoice.amount_before_vat} + {invoice.vat_amount} "
                f"!= {invoice.total_amount}"
            )


def _extract_amounts(text: str, invoice: IsraeliInvoice) -> None:
    """Default (text-layer PDF) amount extractor."""
    _extract_amounts_impl(text, invoice, number_re=NUMBER_RE, to_float=_to_float)


def _extract_amounts_ocr(text: str, invoice: IsraeliInvoice) -> None:
    """OCR-tolerant amount extractor — dot-thousands aware, positional fallbacks."""
    _extract_amounts_impl(
        text,
        invoice,
        number_re=NUMBER_RE_OCR,
        to_float=_to_float_ocr,
        enable_positional_fallbacks=True,
    )


def _extract_allocation_number(text: str, invoice: IsraeliInvoice, *, ocr_tolerant: bool = False) -> None:
    invoice.allocation_number = _find_first(
        r"(?:מספר\s*הקצאה|הקצאה)[^\d]{0,20}(\d{6,})",
        text,
    )
    if invoice.allocation_number is None and ocr_tolerant:
        # Positional fallback: if there's a 15+ digit run on the page and
        # no other label-based match, treat it as the allocation. Israel
        # Tax Authority allocations are typically 24-26 digits.
        m = re.search(r"\b(\d{15,})\b", text)
        if m:
            invoice.allocation_number = m.group(1)
            invoice.extraction_notes.append(
                "allocation_number via long-digit positional fallback"
            )


VENDOR_LABEL_KEYWORDS = (
    "חשבונית", "תאריך", "לכבוד", "מספר",
    "ת.ז", "ח.פ", "ע.מ", "עוסק",
    "סה\"כ", "מע\"מ", "לתשלום", "הנחה",
    "פירוט", "כמות", "מחיר", "ליחידה",
    "פרטי", "אמצעי", "העברה", "בנקאית",
    "נייד", "אימייל", "עמוד", "טלפון",
    "חתימה", "דיגיטלית", "מאובטחת",
    "ממוחשב", "מסמך", "ידי", "הופק",
    "הקצאה", "אסמכתא", "ערך",
    # Common standalone column headers / labels on PassportCard-style forms
    "כתובת", "איש קשר", "תאור מוצר", "מוצר",
)

# Lines that are usually just a label on its own (no value).
# Include token-reversed variants because our normalization reverses
# Hebrew word order per line.
VENDOR_SKIP_EXACT = {
    "כתובת", "לכבוד", "חתימה", "מוצר", "כמות",
    "פירוט", "מספר", "0",
    "איש קשר", "קשר איש",
    "תאור מוצר", "מוצר תאור",
    "מחיר ליחידה", "ליחידה מחיר",
}


def _extract_vendor_name(text: str, invoice: IsraeliInvoice) -> None:
    """
    Pick the vendor name using two signals, in order:
      1. A line (or substring) that ends with the Hebrew company suffix
         "בע\"מ" (equivalent of "Ltd") — strong, template-independent.
      2. First Hebrew-only line that is not a known-label line and does
         not contain an email, URL, or purely numeric content.
    """
    # Signal 1: company name ending in בע"מ. Capture the Hebrew text run
    # (letters, spaces, punctuation) that terminates at the suffix.
    m = re.search(
        r"([֐-׿][֐-׿ \"'().\-]{3,80}?בע[\"״']?\s*מ)",
        text,
    )
    if m:
        candidate = m.group(1).strip()
        # Avoid grabbing a date/number-prefixed line.
        if HEBREW_RE.search(candidate) and not re.search(r"\d{4}", candidate):
            invoice.vendor_name = candidate
            return

    # Signal 2: heuristic walk over lines.
    for line in text.splitlines():
        s = line.strip()
        if not s or s in VENDOR_SKIP_EXACT:
            continue
        if not HEBREW_RE.search(s):
            continue
        if re.fullmatch(r"[\d\s./,\-:]+", s):
            continue
        if any(k in s for k in VENDOR_LABEL_KEYWORDS):
            continue
        if "@" in s or "http" in s.lower():
            continue
        if len(s) < 3:
            continue
        invoice.vendor_name = s
        break


# =============================================================================
# PUBLIC API
# =============================================================================


def extract_text(pdf_path: Path) -> str:
    """Deterministic text-layer extraction via PyMuPDF."""
    import fitz  # PyMuPDF

    doc = fitz.open(pdf_path)
    try:
        chunks = [page.get_text("text") for page in doc]
    finally:
        doc.close()
    return "\n".join(chunks)


def extract_from_text(
    raw_text: str,
    source: Optional[str] = None,
    *,
    reverse_rtl_tokens: bool = True,
    fix_pymupdf_abbreviations: bool = True,
    ocr_tolerant: bool = False,
) -> IsraeliInvoice:
    """
    Extract fields from already-extracted text.

    - For PyMuPDF text layer: keep the defaults.
    - For OCR text (Tesseract, Kraken, Paddle, IronOCR, cloud): pass
      `reverse_rtl_tokens=False`, `fix_pymupdf_abbreviations=False`, and
      `ocr_tolerant=True`. The last flag enables:
        * fuzzy Hebrew label normalization (fixes ח↔מ, ם↔ס, ת↔ח
          mangling in known labels like עוסק מורשה, מע"מ, סה"כ, …)
        * dot-thousands number parsing (Vision's "23.291.70" format)
        * positional VAT fallback via the "18%" marker
        * positional allocation-number fallback (long digit runs)
    """
    invoice = IsraeliInvoice(source_file=source)
    text = normalize_for_matching(
        raw_text,
        reverse_rtl_tokens=reverse_rtl_tokens,
        fix_pymupdf_abbreviations=fix_pymupdf_abbreviations,
    )
    if ocr_tolerant:
        text = fuzzy_normalize_labels(text)

    _extract_business_tax_id(text, invoice)
    _extract_invoice_number(text, invoice)
    _extract_invoice_date(text, invoice)
    if ocr_tolerant:
        _extract_amounts_ocr(text, invoice)
    else:
        _extract_amounts(text, invoice)
    _extract_allocation_number(text, invoice, ocr_tolerant=ocr_tolerant)
    _extract_vendor_name(text, invoice)

    return invoice


def extract_from_pdf(pdf_path: str | Path) -> IsraeliInvoice:
    """
    End-to-end: PDF path -> IsraeliInvoice. Deterministic, no AI calls.
    """
    path = Path(pdf_path)
    raw = extract_text(path)
    return extract_from_text(raw, source=path.name)


# =============================================================================
# CLI
# =============================================================================


def _reconfigure_stdout_utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _cli() -> None:
    _reconfigure_stdout_utf8()

    ap = argparse.ArgumentParser(
        description="Deterministic Israeli-invoice field extractor (no AI)."
    )
    ap.add_argument("path", help="PDF file or directory containing PDFs")
    ap.add_argument(
        "--out", "-o",
        help="Write JSON to this file instead of stdout",
    )
    args = ap.parse_args()

    target = Path(args.path).resolve()
    if not target.exists():
        print(f"Not found: {target}", file=sys.stderr)
        sys.exit(2)

    if target.is_dir():
        pdfs = sorted(target.rglob("*.pdf"))
        if not pdfs:
            print(f"No PDFs under: {target}", file=sys.stderr)
            sys.exit(2)
        results = [asdict(extract_from_pdf(p)) for p in pdfs]
        payload = json.dumps(results, ensure_ascii=False, indent=2)
    else:
        payload = extract_from_pdf(target).to_json()

    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"Wrote: {args.out}", file=sys.stderr)
    else:
        print(payload)


if __name__ == "__main__":
    _cli()
