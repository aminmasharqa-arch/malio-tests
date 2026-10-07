======================================================================
CANONICAL STRUCTURED JSON — NON-NEGOTIABLE
======================================================================

The final extraction output for EVERY invoice must conform to exactly this
business-level JSON structure:

{
  "source_file": "invoice_10032.pdf",
  "vendor_name": "הפניקס חברה לביטוח בע\"מ",
  "business_tax_id": "513000325",
  "business_tax_id_valid": false,
  "invoice_number": "10032",
  "invoice_date": "2026-02-17",
  "amount_before_vat": 23291.7,
  "vat_amount": 4192.51,
  "total_amount": 27484.21,
  "allocation_number": "20260217113816078161849593",
  "extraction_notes": [
    "business_tax_id (עוסק מורשה) has invalid checksum"
  ]
}

THIS IS THE CANONICAL BUSINESS OUTPUT SCHEMA.

Do not replace it with a generic invoice schema.

Do not rename fields.

Do not add buyer fields, currency fields, line items, addresses, phone
numbers, or other fields unless they are kept strictly as internal
benchmark/debug metadata and NOT added to the canonical business JSON.

The benchmark exists to determine which OCR engine most reliably enables
our existing Python extraction pipeline to produce THIS JSON correctly.

======================================================================
FIELD DEFINITIONS
======================================================================

### source_file

Type:
string

Meaning:
Original filename supplied to the extraction pipeline.

Example:

"invoice_10032.pdf"

This field does not come from OCR.

It must be preserved from the processing context.

----------------------------------------------------------------------
### vendor_name

Type:
string | null

Meaning:
The invoice issuer / vendor / business name.

Example:

"הפניקס חברה לביטוח בע\"מ"

This field is especially important for Hebrew OCR.

Evaluate:

- Hebrew character correctness;
- final letters;
- quote characters;
- punctuation;
- whitespace;
- mixed Hebrew/English names;
- word ordering;
- RTL handling.

The benchmark should distinguish:

1. raw exact match;
2. normalized Unicode/string match.

Do not apply aggressive fuzzy correction that could hide OCR errors.

----------------------------------------------------------------------
### business_tax_id

Type:
string | null

Meaning:
The vendor's Israeli business/tax identifier.

Example:

"513000325"

IMPORTANT:

Keep this value as a STRING.

Do not convert it to an integer.

Leading zeroes, when present, must be preserved.

This is a CRITICAL field.

One incorrect digit means the OCR result for this field is incorrect.

----------------------------------------------------------------------
### business_tax_id_valid

Type:
boolean | null

Meaning:
Result of our deterministic checksum/validation logic applied to
business_tax_id.

Example:

false

This is NOT an OCR prediction.

The OCR engine provides characters.

Our Python validation code determines whether the resulting ID passes the
required checksum/validation rules.

This distinction must be preserved in the benchmark.

For example:

OCR:
"513000325"

Validation:
false

The benchmark must separately record:

- Was business_tax_id OCR'd correctly?
- Did our validator correctly classify its validity?

Do not allow the OCR engine's own confidence score to determine
business_tax_id_valid.

----------------------------------------------------------------------
### invoice_number

Type:
string | null

Meaning:
Invoice/reference number identifying the invoice.

Example:

"10032"

Keep as STRING.

Do not convert to integer.

Possible invoice numbers may contain:

- digits;
- leading zeroes;
- hyphens;
- slashes;
- Latin letters;
- Hebrew/Arabic-adjacent text.

Exact correctness is important.

----------------------------------------------------------------------
### invoice_date

Type:
string | null

Canonical format:

YYYY-MM-DD

Example:

"2026-02-17"

OCR may encounter formats such as:

17/02/2026
17.02.2026
17-02-2026
2026-02-17

The downstream normalization layer may convert valid dates into canonical
ISO format.

The benchmark must distinguish:

OCR recognition error

from:

date parsing/normalization error.

Do not give an OCR engine credit for downstream date correction unless
the OCR supplied enough correct evidence to recover the date
deterministically.

----------------------------------------------------------------------
### amount_before_vat

Type:
number | null

Example:

23291.7

Meaning:
Amount before VAT/tax.

This is a CRITICAL financial field.

The benchmark must handle:

23,291.70
23.291,70
23291.70
23 291.70
23,291.7
₪23,291.70

where appropriate.

Normalization must not hide OCR digit mistakes.

----------------------------------------------------------------------
### vat_amount

Type:
number | null

Example:

4192.51

Meaning:
VAT amount.

This is a CRITICAL financial field.

One wrong digit or decimal position counts as incorrect.

----------------------------------------------------------------------
### total_amount

Type:
number | null

Example:

27484.21

Meaning:
Final invoice total.

This is one of the highest-priority fields in the complete benchmark.

One wrong digit, decimal location, sign, or separator interpretation
counts as incorrect.

----------------------------------------------------------------------
### allocation_number

Type:
string | null

Example:

"20260217113816078161849593"

This is an EXTREMELY important OCR benchmark field.

Keep it as STRING.

Do not convert it to integer.

Allocation numbers may be long numeric strings.

A single wrong digit means the field is incorrect.

The benchmark must specifically measure OCR accuracy on long numeric
identifiers because generic CER can hide financially significant errors.

Report:

- exact allocation number accuracy;
- per-digit accuracy;
- common digit confusions;
- insertion/deletion errors;
- truncation;
- accidental spaces;
- reading-order errors.

----------------------------------------------------------------------
### extraction_notes

Type:
array[string]

Example:

[
  "business_tax_id (עוסק מורשה) has invalid checksum"
]

This field contains deterministic warnings, ambiguity notes, validation
failures, and extraction observations.

It is generated by our extraction/validation pipeline.

It is NOT raw OCR text.

Examples may include:

- invalid business ID checksum;
- ambiguous total;
- VAT arithmetic mismatch;
- invoice number not confidently located;
- allocation number missing;
- multiple candidate totals;
- unsupported/ambiguous date;
- low-quality source image.

Use an empty array if there are no notes:

"extraction_notes": []

Do not fabricate explanatory notes.

======================================================================
MISSING VALUES
======================================================================

If a required value cannot reliably be extracted, use:

null

Example:

{
  "allocation_number": null
}

DO NOT:

- invent values;
- use "";
- use "N/A";
- use "unknown";
- guess digits;
- silently substitute a low-confidence candidate.

An explicit null is preferable to a financially dangerous hallucination.

======================================================================
JSON TYPE CONTRACT
======================================================================

The canonical types are:

{
  "source_file": "string",
  "vendor_name": "string|null",
  "business_tax_id": "string|null",
  "business_tax_id_valid": "boolean|null",
  "invoice_number": "string|null",
  "invoice_date": "YYYY-MM-DD|null",
  "amount_before_vat": "number|null",
  "vat_amount": "number|null",
  "total_amount": "number|null",
  "allocation_number": "string|null",
  "extraction_notes": ["string"]
}

Identifiers MUST remain strings.

Financial amounts MUST be numeric after successful normalization.

Dates MUST be canonical ISO strings after successful normalization.

======================================================================
OCR BENCHMARK TARGET
======================================================================

The OCR benchmark is NOT finished when it produces readable text.

Each OCR engine must pass through THE SAME downstream pipeline:

Document/Image
      ↓
OCR engine
      ↓
raw OCR result
      ↓
text/geometry normalization
      ↓
existing Python regex + extraction
      ↓
validation
      ↓
canonical JSON
      ↓
ground truth comparison

The key question is:

"Which OCR engine causes this exact JSON to be correct most often?"

======================================================================
GROUND TRUTH FORMAT
======================================================================

Ground truth should use exactly the same canonical structure.

Example:

{
  "source_file": "invoice_10032.pdf",
  "vendor_name": "הפניקס חברה לביטוח בע\"מ",
  "business_tax_id": "513000325",
  "business_tax_id_valid": false,
  "invoice_number": "10032",
  "invoice_date": "2026-02-17",
  "amount_before_vat": 23291.7,
  "vat_amount": 4192.51,
  "total_amount": 27484.21,
  "allocation_number": "20260217113816078161849593",
  "extraction_notes": [
    "business_tax_id (עוסק מורשה) has invalid checksum"
  ]
}

Claude Code should inspect the current repository before changing any
production schema or existing validation logic.

If the repository already implements these fields, REUSE that
implementation.

Do not create a parallel extraction system solely for the benchmark.

======================================================================
FIELD-LEVEL BENCHMARK METRICS
======================================================================

Every OCR candidate MUST report accuracy for:

1. vendor_name
2. business_tax_id
3. invoice_number
4. invoice_date
5. amount_before_vat
6. vat_amount
7. total_amount
8. allocation_number

Also evaluate:

9. business_tax_id_valid correctness
10. extraction/validation warning correctness

But remember:

business_tax_id_valid and extraction_notes are downstream validation
outputs, not OCR outputs.

The report must therefore distinguish OCR failure from validation/parser
failure.

======================================================================
CRITICAL FIELD EXACT MATCH
======================================================================

Use EXACT MATCH for these fields after approved deterministic
normalization:

business_tax_id
invoice_number
invoice_date
amount_before_vat
vat_amount
total_amount
allocation_number

Do NOT use fuzzy matching for IDs or financial amounts.

For example:

Expected:
"20260217113816078161849593"

OCR:
"20260217113816078161849598"

Result:

INCORRECT

Even though only one character is wrong.

Similarly:

Expected:
27484.21

Extracted:
27484.27

Result:

INCORRECT

======================================================================
COMPLETE INVOICE CORRECTNESS
======================================================================

Create the primary business-level metric:

complete_invoice_correct

A document should count as completely correct only when ALL mandatory
critical fields expected for that ground-truth invoice are correct.

At minimum evaluate:

business_tax_id
invoice_number
invoice_date
amount_before_vat
vat_amount
total_amount

If allocation_number exists in the ground truth, it must also be correct.

vendor_name should also be reported separately and included in an
additional strict-complete score.

Therefore produce at least:

critical_fields_complete_accuracy

and:

all_fields_complete_accuracy

This is much more important to us than CER alone.

======================================================================
FINANCIAL CONSISTENCY CHECKS
======================================================================

Claude Code should preserve or implement deterministic validation such
as:

amount_before_vat + vat_amount ≈ total_amount

subject to legitimate invoice rounding behavior.

However:

Arithmetic consistency MUST NOT be used to silently repair wrong OCR
digits during benchmark scoring.

Example:

OCR might produce three mutually consistent but incorrect values.

That does not make the OCR correct.

Ground truth remains authoritative.

Validation exists to detect problems, not to hide OCR errors.

======================================================================
BUSINESS TAX ID VALIDATION
======================================================================

Claude Code should locate the existing Israeli business/tax ID checksum
implementation in the repository.

If one exists:

reuse it.

Test it thoroughly.

Do not replace it without evidence.

The benchmark should include:

valid IDs
invalid IDs
IDs beginning with zero if applicable
OCR-corrupted IDs

Report separately:

business_tax_id_exact_accuracy

and:

business_tax_id_valid_accuracy

======================================================================
ALLOCATION NUMBER TESTING
======================================================================

Allocation number recognition deserves its own benchmark section.

Measure:

allocation_number_exact_accuracy
allocation_number_digit_accuracy
allocation_number_missing_rate
allocation_number_false_positive_rate

Also produce a digit confusion matrix.

This field is especially useful for comparing OCR engines because it
contains long character sequences where even small OCR defects become
visible.

======================================================================
REQUIRED FINAL BENCHMARK TABLE
======================================================================

The final benchmark summary must include at least:

| Engine | Vendor Name | Business Tax ID | Invoice Number | Date | Before VAT | VAT | Total | Allocation # | Critical Complete | All Fields Complete | p50 Latency | p95 Latency | Cost / 1M Docs |
|--------|-------------|-----------------|----------------|------|------------|-----|-------|--------------|-------------------|---------------------|-------------|-------------|----------------|

Also include:

CER
Hebrew CER
Arabic CER
Digit accuracy
allocation-number digit accuracy

But DO NOT rank primarily by CER.

Our most important ranking metric is:

correct structured invoice JSON.

======================================================================
ERROR ATTRIBUTION
======================================================================

For every wrong field, classify the root cause as one of:

OCR_TEXT_ERROR
OCR_DIGIT_ERROR
OCR_DETECTION_MISS
OCR_READING_ORDER_ERROR
OCR_RTL_ERROR
NORMALIZATION_ERROR
REGEX_ERROR
FIELD_SELECTION_ERROR
VALIDATION_ERROR
SOURCE_IMAGE_UNREADABLE
AMBIGUOUS_DOCUMENT
GROUND_TRUTH_ISSUE
UNKNOWN

This is mandatory.

Without error attribution we cannot know whether changing OCR engines
will actually improve the system.

======================================================================
PRIMARY DECISION METRIC
======================================================================

The final OCR recommendation must primarily answer:

For each 1,000 invoices, how many times does this OCR engine allow our
existing Python pipeline to generate the ENTIRE required structured JSON
correctly?

Then combine this with:

- Hebrew quality;
- Arabic quality;
- numeric accuracy;
- latency;
- infrastructure cost;
- engineering complexity.

The cheapest OCR is NOT the winner if it causes materially more incorrect
invoice JSON.

Likewise, the most expensive OCR is NOT the winner if a nearly-free
self-hosted model achieves essentially the same complete-invoice
accuracy.

The target is:

MAXIMUM CORRECT STRUCTURED JSON
AT
MINIMUM PRACTICAL TOTAL COST.