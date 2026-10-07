"""Format-first invoice extractor for OCR text.

Separate module from `pdf-tests/extractor.py`:
- `pdf-tests/extractor.py` is label-anchored and tuned for PyMuPDF text
  layer output on well-formed PDFs. It expects clean Hebrew labels and
  strict number formats.
- This module is format-first: it enumerates every number / date / long
  digit run / amount candidate in the OCR text, classifies them by
  format, and then disambiguates using WEAK context signals (nearby
  words — even mangled). It never REQUIRES a label match.

Both modules emit the same canonical `IsraeliInvoice` JSON so the rest
of the benchmark harness is unchanged.

Classification strategy, per field:

  business_tax_id   : all 9-digit runs → filter by Israeli checksum →
                      prefer one near a לכבוד / tax-label word (even
                      mangled). Falls back to any valid-checksum ID.
  invoice_number    : look for חשבונית label (fuzzy) + a 3-7 digit int
                      within a short window. Falls back to the first
                      3-7 digit int that isn't a tax ID / date part /
                      allocation / amount.
  invoice_date      : all dd[-/.]mm[-/.]yyyy and yyyy[-/.]mm[-/.]dd →
                      prefer one labeled "תאריך" / "מקור", then first.
  amount_before_vat : if 18% marker is present, the number nearest it
                      is VAT; subtotal = VAT / 0.18, total = subtotal
                      + VAT — both validated against actual numbers in
                      the text (within 1.0 or 0.5% relative tolerance).
                      Falls back to label-anchored extraction.
  vat_amount        : as above.
  total_amount      : as above.
  allocation_number : any 15+ digit run (Israeli tax authority allocation
                      numbers are typically 24-26 digits).
  vendor_name       : line immediately after לכבוד (fuzzy) that is not
                      another label.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import unicodedata
from pathlib import Path
from types import ModuleType
from typing import Any


_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXTRACTOR_PATH = _REPO_ROOT / "pdf-tests" / "extractor.py"


_PDF_EXTRACTOR_CACHE: ModuleType | None = None


def _load_pdf_extractor() -> ModuleType:
    """Load the pdf-tests extractor so we can share schema + checksum.

    Uses a module-local cache rather than sys.modules to avoid clashing
    with the parallel loader in benchmarks/ocr/extract.py that uses the
    same module name.
    """
    global _PDF_EXTRACTOR_CACHE
    if _PDF_EXTRACTOR_CACHE is not None:
        return _PDF_EXTRACTOR_CACHE
    spec = importlib.util.spec_from_file_location(
        "malio_pdf_tests_extractor_schema", _EXTRACTOR_PATH
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load extractor from {_EXTRACTOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    # Register under its own unique key so @dataclass works.
    sys.modules["malio_pdf_tests_extractor_schema"] = module
    spec.loader.exec_module(module)
    _PDF_EXTRACTOR_CACHE = module
    return module


# ---------------------------------------------------------------------------
# Preprocessing — bidi strip + nikud strip + minimal whitespace cleanup
# ---------------------------------------------------------------------------

_BIDI_CTRL = re.compile(r"[‎‏‪-‮⁦-⁩]")
_NIKUD = re.compile(r"[֑-ׇ]")
_MULTI_WS = re.compile(r"[ \t]+")


def _preprocess(text: str) -> str:
    text = _BIDI_CTRL.sub("", text)
    text = _NIKUD.sub("", text)
    text = unicodedata.normalize("NFC", text)
    text = _MULTI_WS.sub(" ", text)
    return text


# ---------------------------------------------------------------------------
# Fuzzy label markers (used only as weak hints, not required)
# ---------------------------------------------------------------------------
# Each entry is a regex that matches a Hebrew label in its mangled OCR
# forms. We DO NOT substitute these — we just use their *positions* as
# proximity hints.

_FUZZY_LABELS: dict[str, re.Pattern[str]] = {
    "לכבוד":       re.compile(r"[לכ][כרבד][בכ][וויי]?[זד]|לכבוד"),
    "עוסק מורשה":  re.compile(r"[עצ/\\.][ווי]?\s*[.9n]?\s*ס[קכ]\s*[חמהנ][וויר]+[רד]?\s*ש[החה]|עוסק\s*מורשה|עוסק|מורשה"),
    "ת.ז/ח.פ":    re.compile(r"[חת]\.?[פזפ]?\s*[./]\s*[תח]?\.?[זפ]?|ח\.?פ|ת\.?ז|ע\.?מ"),
    "חשבונית":    re.compile(r"[חת]ש\s*[בכ][ווי][נן]+[יי]?[תח]|חשבונית"),
    "תאריך":      re.compile(r"[תח][אה][רד][יי][לך]?|תאריך"),
    "מקור":       re.compile(r"\[?\s*[מח][קכ][ווי][רד]\s*\]?|מקור"),
    "לתשלום":     re.compile(r"[לכ][תפ][שסע][לחל][וויי]?[מם]|לתשלום|תשלום"),
    "מע\"מ":      re.compile(r"[מחע][מחע][\"״׳'`]?[מם]|מעמ"),
}


def _label_positions(text: str, label: str) -> list[int]:
    """Return start-index positions of every match of a fuzzy label."""
    pattern = _FUZZY_LABELS.get(label)
    if not pattern:
        return []
    return [m.start() for m in pattern.finditer(text)]


# ---------------------------------------------------------------------------
# Number / date candidate enumeration
# ---------------------------------------------------------------------------

# Any digit sequence of 2+ digits (we filter by length downstream).
_DIGITS_RUN = re.compile(r"\d+(?:[,. ]\d+)*")

# Date patterns. Accept dd[-/. ]mm[-/. ]yyyy or yyyy[-/. ]mm[-/. ]dd.
_DATE_DMY = re.compile(r"(?<!\d)(\d{1,2})[-/.\s](\d{1,2})[-/.\s](\d{4})(?!\d)")
_DATE_YMD = re.compile(r"(?<!\d)(\d{4})[-/.\s](\d{1,2})[-/.\s](\d{1,2})(?!\d)")

# Amount pattern: decimal with 1-2 digits after . or , as decimal sep,
# optionally with , . or space as thousands sep.
# Examples: 23,291.70   23.291.70   1,117.46   947.00   193.22
_AMOUNT_RE = re.compile(
    r"(?<![\d.,])"
    r"(\d{1,3}(?:[,. ]\d{3})+(?:[.,]\d{1,2})?"   # thousands groups + optional decimal
    r"|\d+[.,]\d{1,2}"                             # plain decimal
    r")"
    r"(?![\d])"
)

# 18% marker — anchors VAT reliably across all engines.
_PERCENT_18 = re.compile(r"18(?:[.,]\d{1,2})?\s*%|\(\s*18(?:[.,]\d{1,2})?\s*%?\s*\)")


def _parse_amount(raw: str) -> float | None:
    """Parse '23,291.70' / '23.291.70' / '193.22' / '947' → float.

    Mirrors the logic in pdf-tests/extractor.py:_to_float_ocr but
    standalone here so this module has no runtime dependency on the
    pdf-tests code path beyond the shared schema.
    """
    if raw is None:
        return None
    s = raw.strip()
    for ch in (" ", " ", "₪", "$", "€"):
        s = s.replace(ch, "")
    s = s.lstrip("+")
    if not s:
        return None

    has_comma, has_dot = "," in s, "." in s
    if has_comma and has_dot:
        if s.rfind(".") > s.rfind(","):
            s = s.replace(",", "")
        else:
            s = s.replace(".", "").replace(",", ".", 1).replace(",", "")
    elif has_dot:
        parts = s.split(".")
        if len(parts) > 2:
            if 1 <= len(parts[-1]) <= 2:
                s = "".join(parts[:-1]) + "." + parts[-1]
            else:
                s = "".join(parts)
    elif has_comma:
        parts = s.split(",")
        if len(parts) > 2:
            if 1 <= len(parts[-1]) <= 2:
                s = "".join(parts[:-1]) + "." + parts[-1]
            else:
                s = "".join(parts)
        else:
            if 1 <= len(parts[-1]) <= 2:
                s = parts[0] + "." + parts[-1]
            else:
                s = "".join(parts)
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Per-field extraction
# ---------------------------------------------------------------------------

def _nearest_label_distance(positions: list[int], target: int) -> int:
    if not positions:
        return 10**9
    return min(abs(p - target) for p in positions)


def _extract_tax_id(text: str, validate_israeli_id) -> tuple[str | None, bool | None]:
    """Find the best 9-digit tax ID candidate.

    Preference order:
    1. On a line with ת.ז/ח.פ/עוסק/מורשה (fuzzy label matched)
       AND the line also contains לכבוד or appears below לכבוד
    2. Valid-checksum ID with any tax-label proximity
    3. First valid-checksum 9-digit ID in the text
    4. Any 9-digit ID (returns invalid=False)
    """
    # Enumerate all isolated 9-digit runs (not part of a longer digit string).
    candidates: list[tuple[int, str]] = []
    for m in re.finditer(r"(?<!\d)(\d{9})(?!\d)", text):
        candidates.append((m.start(), m.group(1)))
    if not candidates:
        return None, None

    tax_label_positions = (
        _label_positions(text, "עוסק מורשה")
        + _label_positions(text, "ת.ז/ח.פ")
    )
    lekavod_positions = _label_positions(text, "לכבוד")

    def score(idx: int, value: str) -> tuple[int, int, int, int]:
        """Return a sort key; lower is better."""
        valid = validate_israeli_id(value)
        near_tax_label = _nearest_label_distance(tax_label_positions, idx)
        near_lekavod = _nearest_label_distance(lekavod_positions, idx)
        # Preference: (not valid, far from לכבוד section, far from any
        # tax label, textual position). "near לכבוד" wins because the
        # ground-truth semantic (roadmap §2 convention) picks the לכבוד
        # party's ID.
        return (
            0 if valid else 1,
            near_lekavod,
            near_tax_label,
            idx,
        )

    best = min(candidates, key=lambda c: score(c[0], c[1]))
    chosen = best[1]
    return chosen, bool(validate_israeli_id(chosen))


def _extract_invoice_number(text: str) -> str | None:
    """Short digit sequences adjacent to חשבונית/חשבון label."""
    for label_pos in _label_positions(text, "חשבונית"):
        # Within 40 chars of the label, find a 3-7 digit integer.
        window = text[label_pos:label_pos + 60]
        m = re.search(r"(?<!\d)(\d{3,7})(?!\d)", window)
        if m:
            return m.group(1)
    # Fallback: any 3-7 digit integer that's not part of a date / tax ID /
    # phone number / allocation. We can't know all of these, so prefer the
    # first 4-6 digit integer in the text that's not followed by a date
    # separator or a / character.
    for m in re.finditer(r"(?<!\d)(\d{3,7})(?!\d)", text):
        following = text[m.end():m.end() + 2]
        preceding = text[max(0, m.start() - 2):m.start()]
        if any(ch in "./-" for ch in following + preceding):
            continue
        # Skip 9-digit candidates (tax IDs).
        if len(m.group(1)) == 9:
            continue
        return m.group(1)
    return None


def _extract_date(text: str) -> str | None:
    """Find dd[-/.]mm[-/.]yyyy; prefer labeled with תאריך or מקור."""
    def _iso(day: str, month: str, year: str) -> str | None:
        try:
            d, mo, y = int(day), int(month), int(year)
        except ValueError:
            return None
        if y < 100:
            y += 2000
        if not (1 <= mo <= 12 and 1 <= d <= 31 and 1900 <= y <= 2100):
            # Try swapping day/month in case of US format.
            if 1 <= d <= 12 and 1 <= mo <= 31:
                d, mo = mo, d
                if not (1 <= mo <= 12 and 1 <= d <= 31):
                    return None
            else:
                return None
        return f"{y:04d}-{mo:02d}-{d:02d}"

    def _iso_ymd(y: str, mo: str, d: str) -> str | None:
        return _iso(d, mo, y)

    candidates: list[tuple[int, str]] = []
    for m in _DATE_DMY.finditer(text):
        iso = _iso(*m.groups())
        if iso:
            candidates.append((m.start(), iso))
    for m in _DATE_YMD.finditer(text):
        iso = _iso_ymd(*m.groups())
        if iso:
            candidates.append((m.start(), iso))
    if not candidates:
        return None

    # Prefer dates near "מקור" (original/source), then "תאריך".
    makor_positions = _label_positions(text, "מקור")
    tariq_positions = _label_positions(text, "תאריך")

    def score(idx: int, iso: str) -> tuple[int, int, int]:
        return (
            _nearest_label_distance(makor_positions, idx),
            _nearest_label_distance(tariq_positions, idx),
            idx,
        )

    return min(candidates, key=lambda c: score(c[0], c[1]))[1]


def _extract_allocation(text: str) -> str | None:
    """15+ digit run."""
    for m in re.finditer(r"(?<!\d)(\d{15,})(?!\d)", text):
        return m.group(1)
    return None


def _extract_amounts(text: str) -> tuple[float | None, float | None, float | None]:
    """Return (amount_before_vat, vat_amount, total_amount).

    Primary signal: an 18% marker. The amount nearest to it is VAT;
    subtotal = VAT / 0.18; total = subtotal + VAT. Both validated by
    finding real numbers near the computed targets.

    Fallback: look for amounts next to labels (even mangined). We treat
    this as a weak hint only; the 18% marker is far more reliable across
    engines.
    """
    # 1. Collect every amount candidate with its text position.
    amounts: list[tuple[int, float]] = []
    for m in _AMOUNT_RE.finditer(text):
        v = _parse_amount(m.group(1))
        if v is not None and 0 < v < 10_000_000:
            amounts.append((m.start(), v))
    if not amounts:
        return None, None, None

    # 2. PRIMARY signal: find pairs (a, b) where a * 1.18 ≈ b. Rank by
    # how close they are in the text (same invoice section wins). This
    # catches the Vision photo_2 case where an engine mis-OCR'd ₪34.78
    # as "R134.78" — the 134.78 isn't the real VAT, but 193.22 + 34.78
    # ≈ 228.00 still shows up as a valid arithmetic pair in the text.
    pct_match = _PERCENT_18.search(text)
    pct_pos = pct_match.start() if pct_match else None

    subtotal_val: float | None = None
    vat_val: float | None = None
    total_val: float | None = None

    best_pair_score: float | None = None
    for i, (pos_a, a) in enumerate(amounts):
        if a <= 0:
            continue
        expected_b = a * 1.18
        tol = max(0.5, expected_b * 0.005)
        for j, (pos_b, b) in enumerate(amounts):
            if i == j:
                continue
            if abs(b - expected_b) > tol:
                continue
            if b <= a:
                continue
            # Score: physical proximity in text (closer = better), with a
            # bonus if the pair surrounds or sits next to the 18% marker.
            score = abs(pos_a - pos_b)
            if pct_pos is not None:
                score += 0.1 * min(abs(pos_a - pct_pos), abs(pos_b - pct_pos))
            if best_pair_score is None or score < best_pair_score:
                best_pair_score = score
                subtotal_val = a
                total_val = b
                vat_val = round(b - a, 2)

    # 3. SECONDARY signal: if no arithmetic pair found but there IS a
    # standalone VAT via the 18% marker, use it.
    if vat_val is None and pct_match is not None:
        pct_start, pct_end = pct_match.start(), pct_match.end()
        outside = [a for a in amounts if not (pct_start <= a[0] < pct_end)]
        if outside:
            candidate = min(outside, key=lambda a: min(abs(a[0] - pct_start), abs(a[0] - pct_end)))
            if min(abs(candidate[0] - pct_start), abs(candidate[0] - pct_end)) <= 60:
                vat_val = candidate[1]

    # 4. TERTIARY: label-anchored VAT.
    if vat_val is None:
        for mv_pos in _label_positions(text, "מע\"מ"):
            window_start, window_end = max(0, mv_pos - 30), mv_pos + 30
            nearby = [a for a in amounts if window_start <= a[0] <= window_end]
            if nearby:
                vat_val = min(nearby, key=lambda a: abs(a[0] - mv_pos))[1]
                break

    # 5. If we have VAT but not subtotal/total, infer via 18% arithmetic.
    if vat_val is not None and 0 < vat_val < 1_000_000:
        if subtotal_val is None or total_val is None:
            expected_subtotal = vat_val / 0.18
            expected_total = expected_subtotal + vat_val

            def _closest(expected: float) -> float | None:
                tol = max(1.0, expected * 0.005)
                best: tuple[float, float] | None = None
                for _, v in amounts:
                    diff = abs(v - expected)
                    if diff <= tol and (best is None or diff < best[0]):
                        best = (diff, v)
                return best[1] if best else None

            if subtotal_val is None:
                subtotal_val = _closest(expected_subtotal)
            if total_val is None:
                total_val = _closest(expected_total)

    # 6. Fallback for total: label-anchored "לתשלום".
    if total_val is None:
        for pos in _label_positions(text, "לתשלום"):
            window_start, window_end = max(0, pos - 30), pos + 60
            nearby = [a for a in amounts if window_start <= a[0] <= window_end]
            if nearby:
                total_val = min(nearby, key=lambda a: abs(a[0] - pos))[1]
                break

    # 7. If we have total and VAT but no subtotal, derive it.
    if subtotal_val is None and total_val is not None and vat_val is not None:
        candidate = total_val - vat_val
        if candidate > 0:
            for _, v in amounts:
                if abs(v - candidate) <= max(1.0, candidate * 0.005):
                    subtotal_val = v
                    break

    return subtotal_val, vat_val, total_val


_HEBREW_WORD = re.compile(r"[א-ת][א-ת֑-ׇ\"'״׳\s]+[א-ת]")


def _extract_vendor_name(text: str) -> str | None:
    """The business name near the לכבוד label (counterparty, roadmap §2)."""
    for pos in _label_positions(text, "לכבוד"):
        # Look at the ~120 chars after the label (same line + next line or two).
        window = text[pos:pos + 150]
        # Skip the label text itself (variable char count because of fuzzy
        # matching), then grab the first Hebrew-dominant phrase.
        after_colon = window.split(":", 1)[-1] if ":" in window[:30] else window
        # Split into non-empty lines; take the first line that has
        # a Hebrew word of length >= 3 and no digit.
        for line in re.split(r"[\n\r]+", after_colon):
            line = line.strip(" .,;:|[]()")
            if not line or any(ch.isdigit() for ch in line):
                continue
            # Must contain Hebrew letters, not just Latin / symbols.
            if not re.search(r"[א-ת]{2,}", line):
                continue
            # Trim to the first non-trivial Hebrew phrase (stop at
            # trailing noise like "ח.פ." or email addresses).
            m = re.search(r"([א-ת][א-ת\s\"'״׳.]*[א-ת])", line)
            if m:
                phrase = m.group(1).strip()
                # Reject very short labels like "לכבוד" that may remain.
                if len(phrase) >= 3 and phrase not in ("לכבוד", "לכבוז", "לכבוד:"):
                    return phrase
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_from_ocr_text(text: str, source: str | None = None) -> Any:
    """Extract canonical IsraeliInvoice JSON from OCR text.

    This is the OCR-first path. For PyMuPDF text-layer PDFs, use
    `pdf-tests/extractor.extract_from_text` instead.
    """
    pdf_mod = _load_pdf_extractor()
    invoice = pdf_mod.IsraeliInvoice(source_file=source)

    cleaned = _preprocess(text)

    tax_id, tax_id_valid = _extract_tax_id(cleaned, pdf_mod.validate_israeli_id)
    invoice.business_tax_id = tax_id
    invoice.business_tax_id_valid = tax_id_valid
    if tax_id is not None and tax_id_valid is False:
        invoice.extraction_notes.append(
            "business_tax_id (עוסק מורשה) has invalid checksum"
        )

    invoice.invoice_number = _extract_invoice_number(cleaned)
    invoice.invoice_date = _extract_date(cleaned)
    invoice.allocation_number = _extract_allocation(cleaned)
    invoice.vendor_name = _extract_vendor_name(cleaned)

    subtotal, vat, total = _extract_amounts(cleaned)
    invoice.amount_before_vat = subtotal
    invoice.vat_amount = vat
    invoice.total_amount = total

    if subtotal is not None and vat is not None and total is not None:
        if abs(subtotal + vat - total) > 1.0:
            invoice.extraction_notes.append(
                f"amount check mismatch: {subtotal} + {vat} != {total}"
            )

    return invoice
