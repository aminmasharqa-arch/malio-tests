# third_party

Source trees for OCR candidates that aren't on PyPI. They're installed
into the benchmark venv via `pip install -e .` so adapters can import
them under their real package names. The clones themselves are
**gitignored** — the pinned commit SHAs below are the source of truth.

## RivoksLab/paddleocr-hebrew — candidate #1

Hebrew-specific OCR pipeline (ONNX Runtime inference; no PaddlePaddle at
runtime). Apache-2.0 code and weights.

### Install

```bash
cd ocr-benchmarks/third_party
git clone https://github.com/RivoksLab/paddleocr-hebrew
cd ../..
.venv/Scripts/python -m pip install -e ocr-benchmarks/third_party/paddleocr-hebrew huggingface_hub
```

Record the clone's `git rev-parse HEAD` in your run manifest for
reproducibility.

### Models

~180 MB of ONNX models auto-download from
[`Rivok/paddleocr-hebrew`](https://huggingface.co/Rivok/paddleocr-hebrew)
into `~/.cache/huggingface` on first use. Smoke-test:

```bash
.venv/Scripts/python -c "
from paddleocr_hebrew import HebrewOCR
ocr = HebrewOCR.word('<models_dir>', providers=['CPUExecutionProvider'])
print(ocr.read('ocr-benchmarks/images-tests/photo_2 (2).jpg')['meta'])
"
```

For a reproducible run, pin a specific snapshot by passing
`params.models_dir: <path>` in `phase1.yaml`. Omit it to accept the
latest HF snapshot (unpinned).

### Caveat

Arabic is **unsupported** by this candidate. The report keeps that
visible in its coverage column — do not route Arabic documents here.
