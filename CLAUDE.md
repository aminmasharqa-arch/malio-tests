# malio-tests

Scratch workspace for Malio's OCR / PDF extraction experiments. The real Malio app lives elsewhere (`C:\dev\malio\`); this repo is for prototypes, benchmarks, and throwaway tests feeding into that work.

## Layout

```
malio-tests/
├── ocr-benchmarks/         # Phase 1+ OCR benchmark harness (the current active work)
│   ├── OCR_BENCHMARK_ROADMAP.md   # Authoritative scope & plan — read before changes here
│   ├── benchmarks/         # Python package: adapters, scorer, CLI (skeleton stage)
│   │   ├── configs/        # phase1.yaml etc. (empty)
│   │   ├── data/           # manifests, ground-truth (empty)
│   │   ├── ocr/adapters/   # one adapter per engine (empty)
│   │   └── runs/           # dated run outputs
│   └── images-tests/       # sample images (Hebrew photos, handwritten)
├── ocr-tests/              # Early Tesseract exploration (test_ocr.py)
│   ├── test_ocr.py         # pytesseract loop over PSM/lang on photo_2.jpg
│   ├── .venv/              # local venv
│   └── results/            # per-config .txt + .csv + boxed previews
├── pdf-tests/              # Deterministic PyMuPDF-based invoice extractor
│   ├── extractor.py        # Pure Python Hebrew invoice extractor (NO OCR, text-layer PDFs)
│   ├── test_invoice_extraction.py  # Batch runner producing per-PDF JSON
│   ├── export_csv.py
│   ├── requirements.txt    # PyMuPDF>=1.24.0
│   ├── invoices-pdf-testing/Invoices-in/   # Input PDFs
│   ├── new-pdf-250-invoices-carmel-tests/  # Larger input set
│   ├── results/, results-carmel/           # Per-PDF output dirs with parsed.json
│   └── hebrew-ocr-forms/   # Submodule-like reference for Hebrew normalization skill
└── mdfilesforprompts/
    └── prompt.md           # Canonical invoice-JSON spec used as the prompt contract
```

## The canonical invoice JSON (non-negotiable)

Every extraction — OCR or text-layer — must produce exactly this shape. Full spec in `mdfilesforprompts/prompt.md` and section 2 of `ocr-benchmarks/OCR_BENCHMARK_ROADMAP.md`.

```json
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
  "extraction_notes": ["business_tax_id (עוסק מורשה) has invalid checksum"]
}
```

Rules: IDs are strings (preserve leading zeros); missing scalars are `null` not `0` / `""`; `extraction_notes` is a deterministic list (empty when none); `business_tax_id_valid` is `true | false | null`. Do not add buyer, currency, or line-item fields to the canonical object — only to debug metadata outside it.

## Running things

Both subprojects use **separate Python environments**. Don't try to share deps.

**ocr-tests** (requires Tesseract at `C:\Program Files\Tesseract-OCR\tesseract.exe`):
```bash
cd ocr-tests && .venv/Scripts/python test_ocr.py
```

**pdf-tests** (PyMuPDF only, text-layer PDFs — no OCR):
```bash
cd pdf-tests && python extractor.py <pdf-or-folder>
cd pdf-tests && python test_invoice_extraction.py <path> --out results
```

**ocr-benchmarks** (planned CLI, not yet wired):
```bash
python -m benchmarks.ocr inspect-repo
python -m benchmarks.ocr validate-truth --manifest benchmarks/data/manifest.jsonl
python -m benchmarks.ocr run --config benchmarks/configs/phase1.yaml --split validation
python -m benchmarks.ocr report --run-dir benchmarks/runs/<run_id>
```

## Conventions

- **Hebrew is first-class.** Tesseract configs use `heb+eng` / `ara+eng`; never strip bidi controls, nikud, or final-form letters without logging it. See `pdf-tests/extractor.py` header comment for the normalization pipeline.
- **No AI/LLM at extraction time** in `pdf-tests/extractor.py` — it's deterministic PyMuPDF + regex + Israeli-ID checksum. Keep it that way.
- **OCR benchmark candidates (exactly six, all local — no cloud OCR, no LLM/VLM at extraction time):** RivoksLab/paddleocr-hebrew; Tesseract + tessdata_best; Tesseract + tessdata_fast; Kraken; IronOCR; ABBYY FineReader Engine. Full matrix in roadmap §4. Blocked integrations stay in the report as `BLOCKED`; never substitute a seventh engine.
- Decimal amounts use Python `Decimal` for comparison; JSON serializes as numbers; no NaN/infinity.
- **Reproducibility:** every run must record engine/package version, model revision, weight SHA-256, renderer, preprocessing, and preprocessor. Mutable `latest`/`main` tags are not reproducible.

## Working style

- Repo root is `C:\dev\tests\malio-tests`. Shell is bash on Windows — use Unix-style paths (`/`) in commands, forward slashes.
- When editing `ocr-benchmarks/`, treat `OCR_BENCHMARK_ROADMAP.md` as the brief and surface drift between code and brief rather than silently diverging.
- `mdfilesforprompts/prompt.md` is a long-form schema contract — treat it as the source of truth for field semantics, not a draft.
- Status at session start usually shows many sibling untracked repos under `C:\dev\` and `C:\dev\tests\` — those are unrelated projects. Only touch files under `malio-tests/`.
