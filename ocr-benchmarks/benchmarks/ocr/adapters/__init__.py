"""OCR engine adapters. One module per engine, each exposing a class that
satisfies the `OCRAdapter` protocol in `benchmarks.ocr.types`.

Candidates (roadmap §4) — all local, no external OCR/LLM/VLM:
  1. RivoksLab/paddleocr-hebrew  → paddleocr_hebrew.py
  2. Tesseract + tessdata_best   → tesseract.py (tessdata_dir points at _best)
  3. Tesseract + tessdata_fast   → tesseract.py (tessdata_dir points at _fast)
  4. Kraken                       → kraken.py
  5. IronOCR                      → ironocr.py (local .NET bridge)
  6. ABBYY FineReader Engine      → abbyy.py   (local SDK bridge)

A missing engine binary/trial/model is a `BLOCKED` row in the report, not a
reason to drop the candidate or substitute a different one.
"""
