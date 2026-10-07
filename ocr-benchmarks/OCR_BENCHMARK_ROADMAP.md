# OCR_BENCHMARK_ROADMAP

Research checked: **2026-10-07**. Scope: exactly six local/self-hosted OCR candidates for Hebrew-first Israeli invoice extraction, especially phone photos, with Arabic and English requirements retained. This is an implementation brief for Claude Code in the existing repository. No invoice benchmark has been run for this report.

## 1. Executive Recommendation

**Benchmark these six and only these six:** RivoksLab/paddleocr-hebrew; Tesseract + tessdata_best; Tesseract + tessdata_fast; Kraken; IronOCR; ABBYY FineReader Engine. Each is a required candidate. Modes, model sizes, language packs, and preprocessing variants remain configurations within these six, never additional candidates.

The objective is to measure how often local OCR plus the existing deterministic Python extraction pipeline produces correct invoice JSON **without an external LLM/VLM call**. OCR may use neural networks internally. All recognition in this benchmark runs locally; no hosted OCR service, paid VLM, LLM field extraction, or LLM text repair belongs in the comparison.

Implement the shared harness and Tesseract best/fast first, then the Hebrew PaddleOCR specialist and Kraken. Prepare IronOCR and ABBYY local adapters during the same screening phase. Evaluate all six on the same labeled inputs wherever script support permits. Integration order does not authorize dropping candidates; unavailable commercial trials/licenses remain explicit `BLOCKED` rows with the steps needed to complete them.

Choose by complete-invoice correctness, silent financial errors, latency, and cost per correct invoice. Hebrew PaddleOCR is a particularly relevant specialist to test, not a proven winner. No quality, production throughput, routing proportion, or correction effort is established until measured. All proposed settings and calculated cost scenarios below are labeled as such.

## 2. Requirements

Every engine must follow the same path:

`page image → OCR adapter → shared normalization → existing extraction/regex → shared deterministic validation → canonical JSON → ground-truth comparison`

Preserve exactly these keys and types; do not add business fields:

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
  "extraction_notes": [
    "business_tax_id (עוסק מורשה) has invalid checksum"
  ]
}
```

- Identifiers are strings, preserving leading zeros and every digit. Missing extracted scalar values are `null`, never guessed, empty strings, or zero.
- `source_file` identifies the input; `extraction_notes` is a deterministic list, empty when there are no notes. `business_tax_id_valid` is `true`, `false`, or `null` when unavailable/not applicable.
- Validation computes validity and notes. It must retain an accurately extracted invalid printed ID. It must not repair digits to satisfy a checksum or replace printed amounts to satisfy arithmetic.
- Reuse the repository's Israeli checksum implementation. Do not apply it automatically to foreign Arabic/English tax IDs; jurisdiction belongs in internal metadata, with unsupported validation returning `null`.
- Use `Decimal` for amounts and comparisons, serialize canonical amounts as JSON numbers, and prohibit NaN/infinity. Missing VAT is different from explicitly printed zero VAT. Do not fabricate an allocation number on a document where it is absent.
- Preserve raw OCR, page geometry, confidence, candidate-field evidence, versions, timings, and error labels **outside** the canonical object.
- Support scanned PDFs, JPG/JPEG, PNG, phone photos, receipts, and multi-page invoices. One document must contain one invoice; flag files containing several invoices for separate handling.
- Render PDFs to common images for the main OCR comparison, for every selected engine. Report native digital PDF text extraction separately as a control, never as an OCR victory or as a seventh OCR candidate.

## 3. Candidate Research & Scope

The six-candidate list is fixed. Do not add substitutes, cloud reference services, other repositories, or extra engines when an integration is inconvenient. Public project/vendor evaluations are supporting research, not independent evidence of Israeli invoice correctness.

| Selected candidate | Verified basis for testing | Research limitation to resolve in our benchmark |
|---|---|---|
| RivoksLab/paddleocr-hebrew | Hebrew-specific page pipeline and downloadable ONNX inference models; mixed Hebrew/English/digit handling; code and weights Apache-2.0. [S1–S2] | Project-reported evaluation; phone-invoice field correctness unmeasured. Training weights/configurations are not published. Arabic is outside the documented character set. |
| Tesseract + tessdata_best | Official Hebrew, Arabic, and English traineddata; LSTM recognition; Apache-2.0. [S3–S5] | Recognition and reading order on glare, skew, small print, and mixed RTL numbers require measurement. |
| Tesseract + tessdata_fast | Official faster traineddata for the same required languages; Apache-2.0. [S3,S4,S6] | Speed and exact field accuracy must be measured against best on paired inputs. |
| Kraken | Current 7.1 release supplies multilingual recognition models and project evaluations covering Hebrew, Arabic, and English. Engine and the selected medium recognition model are Apache-2.0. [S7–S9] | Models emphasize historical handwritten/printed material; transfer to modern invoice photos is unmeasured. Verify and pin the segmentation model and its license too. |
| IronOCR | Commercial .NET library with local processing, Hebrew/Arabic/English packs, preprocessing, and structured OCR output. [S10–S12] | Tesseract-derived recognition: measure the value of packaging/preprocessing versus direct Tesseract. Verify exact package, packs, deployment rights, and license cost. |
| ABBYY FineReader Engine | Commercial local OCR SDK; required languages and camera-image processing are documented. [S13–S15] | Verify installed SDK build, OS, language modules, export/bridge capabilities, and a deployment-specific license quote. Invoice-photo performance remains unmeasured. |

Commercial OCR SDK/library fees are allowed within the selected six; paid external LLM/VLM calls are outside scope. Never substitute a vendor's cloud product for its selected local library/SDK.

## 4. The Six OCR Candidates

Every row below is mandatory. This is a testing order, not a quality ranking. Language support does not guarantee accurate mixed-script invoice extraction.

| # | Engine / repository | Initial configuration to pin | Hebrew / Arabic / English | Execution and adapter | Cost/license |
|---:|---|---|---|---|---|
| 1 | [RivoksLab/paddleocr-hebrew](https://github.com/RivoksLab/paddleocr-hebrew) | `server-svtrv2` recognizer/cascade with `word-det`; compare `line-det` within this candidate | Hebrew and mixed English/digits documented; Arabic unsupported; standalone English quality to test | ONNX Runtime CPU first; optional GPU. Python adapter preserves native geometry/confidence | Apache-2.0 code and weights; compute/operations |
| 2 | [Tesseract + tessdata_best](https://github.com/tesseract-ocr/tessdata_best) | Tesseract 5.5.3 target, pinned `heb/ara/eng` traineddata, `--oem 1` | All three documented | CPU; Python/CLI TSV or hOCR adapter | Apache-2.0 engine and weights; compute/operations |
| 3 | [Tesseract + tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast) | Same Tesseract binary/settings; separate pinned fast traineddata, `--oem 1` | All three documented | Same CPU adapter; distinct candidate/config ID | Apache-2.0 engine and weights; compute/operations |
| 4 | [Kraken](https://github.com/mittagessen/kraken) | 7.1 target; multilingual `medium.safetensors` from Zenodo record 21788410 plus compatible pinned segmentation model | Hebrew, Arabic, English appear in project evaluation | CPU first; GPU when available. Python/CLI adapter, native structured output | Apache-2.0 engine and selected medium recognizer; check every additional model license |
| 5 | [IronOCR](https://ironsoftware.com/ocr/csharp/languages/hebrew/) | Existing .NET experiment first; freeze IronOcr package, runtime, Hebrew/Arabic/English packs and filters | All three documented | Local C# bridge returning OCR-only JSON to Python; CPU baseline | Commercial library; applicable license plus compute/operations |
| 6 | [ABBYY FineReader Engine](https://www.abbyy.com/ocr-sdk/) | FineReader Engine 12 target; freeze installed build, language modules and recognition/image-processing settings | All three documented; verify licensed modules | Local supported SDK bridge returning OCR-only output to Python; CPU baseline | Commercial SDK; actual quoted license plus compute/operations |

**Within-candidate configurations:** Hebrew PaddleOCR word and line detection are required development comparisons. Begin with the documented flagship recognizer/cascade; lighter recognizers can be tested later for a measured latency/memory gap. Kraken small/tiny are optional configurations after medium establishes a quality baseline. IronOCR language-pack tiers and SDK filters are configurations, not new candidates. Show configuration results separately while retaining exactly six top-level candidate groups.

**Pinning rule:** record engine/package version, source commit, every model/dictionary SHA-256, model revision, detector, recognizer, renderer, preprocessing, runtime/backend, precision, device, and thread count. Resolve exact commits/builds when installing; never invent a revision or leave mutable `main`/`latest` unrecorded. Commercial package/build and language-pack identifiers are required even when vendor model weights are inaccessible. Missing geometry/confidence stays null; never fabricate it or treat confidence scores as comparable across engines.

## 5. Hebrew / Arabic Analysis

**Hebrew:** prioritize real Israeli vendor layouts, company suffixes, final letters `ך ם ן ף ץ`, ₪, thermal receipts, small print, and Hebrew labels next to LTR numbers/English. Keep separate tests for issuer IDs versus customer IDs, invoice numbers versus order/receipt numbers, and allocation numbers versus unrelated long numbers. Annotate field location and source label so attribution distinguishes wrong recognition from wrong selection.

**Arabic:** test connected script, Arabic/English labels, both `0123456789` and `٠١٢٣٤٥٦٧٨٩`, plus Persian digits when present. Apply shared Unicode decimal-digit mapping with a trace; retain the original string. Test `٫` versus `٬` and Western comma/period conventions without globally deleting punctuation. Ambiguous dates/decimal conventions require a frozen jurisdiction policy or an uncertainty note, not a guess.

**Ordering:** preserve engine text, page/region geometry, reading order, and text-direction metadata. Never reverse whole RTL lines: that corrupts numbers, decimals, dates, and English. Use logical Unicode text for extraction and bidi rendering only for display. Evaluate the repository's current text assembly first, then a single shared geometry-aware assembly as a separate ablation for every engine. Do not create per-engine regex rules. Block-only engines must retain coarse geometry; do not invent word boxes or word confidences.

Use modest NFC/whitespace normalization first. Any compatibility normalization, punctuation folding, tatweel/diacritic handling, or bidi-control removal must be logged and tested for digit/identifier preservation. Vendor-name scoring should not collapse distinct Hebrew final letters or Arabic letters into equivalence.

After standalone results, test only combinations of the selected six: **A** one local engine covering all required scripts; **B** Hebrew PaddleOCR for Hebrew plus a selected multilingual engine for Arabic/English; **C** Tesseract fast primary plus a stronger selected local fallback. Routing must use runtime evidence. Dataset language labels may describe an oracle ceiling, but cannot drive a deployable router. Measure misrouting, extra passes, and the fraction accepted correctly without external LLM/VLM calls. The Hebrew specialist's unsupported Arabic subset must remain visible; it cannot be presented as a universal solution.

## 6. Benchmark Design

All counts/splits below are proposed experimental settings, not measured workload percentages. **All six must receive a benchmark attempt and a reported status; no candidate is silently removed after screening.**

| Stage | Documents and separation | Required work | Exit condition |
|---|---|---|---|
| Phase 1 | ~100: 60 development, 40 screening validation | Integrate and attempt all six; Hebrew Paddle word/line comparison; commercial access tracked explicitly | Working adapters, adjudicated labels, per-candidate results or documented blockers; no production selection |
| Phase 2 | ~500 total: 300 development, 200 independent validation | Compare all six available candidates, resolve blockers, freeze within-candidate settings and local routing | Reproducible quality/cost tradeoffs and frozen final protocol |
| Phase 3 | 1,000–2,000+ representative total; example 600 dev + 400 validation + 1,000 new holdout = 2,000 | Final paired comparison of all six available candidates and frozen local routers | Holdout report with intervals; disclose any still-blocked candidate as unfinished |

Do not claim the requested six-candidate benchmark is complete while a candidate remains `BLOCKED` or `NOT RUN`. Continue useful comparisons and specify exactly what is needed to finish. `UNSUPPORTED` applies to an unsupported script/configuration, not a reason to omit that subset from coverage. A weak candidate still receives an honest result; expand fine tuning only where justified.

Never reuse exposed Phase 1/2 samples as the final holdout. Split by vendor/template family and capture session, deduplicate exact/perceptual copies, and keep every page/augmentation of one invoice in the same split. Report familiar-template and unseen-vendor performance separately. Do not tune regex, language order, thresholds, rendering, or preprocessing on final holdout.

Suggested Phase 1 language quotas: **65 Hebrew/Hebrew+English, 25 Arabic/Arabic+English, 10 English**. These are coverage targets, not a production mix estimate. Include clean scans and phone photos, receipts/thermal print, small/long numeric identifiers, and multi-page documents. Tag low resolution, blur, rotation/skew, glare, shadow, and perspective distortion; overlapping tag counts do not sum to document count. Grow allocation-positive and Arabic subsets if denominators are inadequate. Make phone-photo results prominent rather than hiding them in a scan-heavy average.

Run the same images first after common EXIF orientation and PDF rasterization. Initial PDF setting: **300 DPI**, validated against **200 DPI** on development data. Keep originals, dimensions, and coordinate transforms. Engine-internal resize is part of that configuration. Process every invoice page.

Next compare a common capped preprocessing arm (grayscale, deskew, contrast; perspective correction when justified). Apply it consistently. Record IronOCR/ABBYY and other engine-internal filters separately; those are part of the configured local product. Count every pass in latency/cost. Freeze variant selection from runtime signals on development data; never pick whichever image or engine scored best using ground truth.

Use common rasterized images for the main benchmark. Native PDF handling and embedded digital text may be separate operational controls, never additional OCR candidates or a hidden advantage in the main comparison. Missing hardware, model downloads, trials, licenses, or language modules produce specific `BLOCKED` records, not invented results.

## 7. Metrics & Ground Truth

Ground truth contains the canonical JSON plus a separate annotation record: document hash, split, language/scripts, jurisdiction, quality tags, field presence (`PRESENT`, `ABSENT`, `UNREADABLE`, `AMBIGUOUS`), printed value, page/region, and adjudication. Two humans independently transcribe critical identifiers/amounts; resolve disagreement from the source. Do not generate truth from the OCR being evaluated. Transcribe full pages or a fixed representative line subset for CER/WER; report its denominator and selection method.

Default critical set: **`business_tax_id`, `invoice_number`, `total_amount`, `allocation_number`**. Also report a financial set covering all three monetary fields plus date. Freeze these sets before validation.

| Metric | Exact scoring rule |
|---|---|
| Each of the eight business fields | Correct / scorable fields; separately present-field exact accuracy, absent-field specificity, and missing/unreadable/ambiguous counts |
| IDs and invoice/allocation numbers | Exact string equality after approved deterministic digit mapping/outer whitespace handling; no edit-distance tolerance, integer conversion, or lost zeros |
| Date | Exact ISO date under the frozen date interpretation policy; an ambiguous source is not assigned a convenient date |
| Before VAT / VAT / total | Exact printed decimal value at annotated precision using `Decimal`; no scoring tolerance that hides a wrong digit. Arithmetic rounding tolerance belongs only in validation |
| Vendor name | Primary exact match after NFC and whitespace normalization; secondary approved punctuation equivalence reported separately, never fuzzy matching as the primary score |
| `critical_fields_complete_accuracy` | Documents where every critical field matches / fully scorable documents; present fields must match, known-absent fields must be null |
| `all_fields_complete_accuracy` | Documents where all eight extracted business fields match / fully scorable documents |
| Canonical contract accuracy | Correct schema, `source_file`, validator boolean and deterministic notes; report separately from OCR field accuracy |
| CER / WER | Levenshtein substitutions + deletions + insertions divided by reference characters/words; specify tokenization, normalization, line alignment and macro/micro aggregation |
| Hebrew/Arabic CER | Script-specific reference segments, with aligned insertions assigned to their segment; missing detected lines count as deletions |
| Digit accuracy | `max(0, 1 − digit_edit_distance/reference_digit_count)` on aligned annotated numeric spans; report insertion count and exact numeric-span accuracy too |
| Allocation digit accuracy | Same rule on allocation strings; missing allocation is zero accuracy when present; exact allocation accuracy remains decisive |

Known-absent fields count only when absence is adjudicated. Returning null for a present field fails. Documents with unreadable/ambiguous required truth are excluded from that exact-match denominator **and their excluded counts and all-submission coverage are displayed**. Report a conservative resolved-correct/all-submissions yield separately; never improve an engine's score by dropping OCR failures. Track documents with no present critical fields as a separate group so absence-only passes cannot dominate.

Measure **auto-accept coverage, precision among accepted invoices, false acceptance rate, and routed fallback fraction** against adjudicated truth. Presence, confidence, valid checksum, and amount consistency cannot prove correctness. A valid but wrong digit sequence can pass validation.

Report counts and **95% Wilson intervals** for exact proportions; use vendor-group paired bootstrap intervals for engine differences and weighted aggregates. Approximately 100 documents screen engineering, not rare silent errors. As a calculated illustration, zero observed errors in 100 independent accepted invoices still gives an approximately **3%** one-sided upper error bound using `3/n`; 1,000 gives approximately **0.3%**. Correlated templates weaken this inference. Proposed sample sizes alone do not certify production quality.

**Error attribution:** retain raw text → assembled text → normalized text → regex candidates → selected values → validated JSON. Re-run the frozen parser on manually correct transcription in the same geometry representation to isolate OCR from downstream failure. Attribute from field evidence; automatic string difference alone is insufficient.

| Label | Evidence required |
|---|---|
| `OCR_TEXT_ERROR` | Correct region detected, non-digit transcription wrong |
| `OCR_DIGIT_ERROR` | Digit substitution/deletion/insertion in detected target region |
| `OCR_DETECTION_ERROR` | Legible target region omitted or cut off |
| `OCR_RTL_ORDER_ERROR` | Recognized tokens present but reading/run order corrupted |
| `NORMALIZATION_ERROR` | Correct raw tokens damaged by normalization/assembly |
| `REGEX_ERROR` | Correct normalized target text present but candidate not extracted |
| `FIELD_SELECTION_ERROR` | Correct candidate present; buyer ID, subtotal, order number, other page, etc. selected |
| `VALIDATION_ERROR` | Correct extracted value incorrectly rejected/changed or validity/notes wrong |
| `UNREADABLE_SOURCE` | Human adjudication cannot read the target; not merely an OCR failure |
| `UNKNOWN` | Insufficient evidence |

Allow one primary and multiple contributing labels. Preserve stage flags for causal chains. Annotators should not see engine names while adjudicating a comparative sample where practical.

## 8. Python / Claude Code Implementation Plan

**Implement incrementally in the existing repository, then benchmark all six selected candidates. Do not add any other OCR engine.** Repository paths and APIs below are proposed; adapt them after inspection, preserving existing conventions.

1. **Inspect:** read applicable `AGENTS.md`, inventory using `rg --files`, locate OCR calls, normalization, regex, tax checksum, canonical serialization, and tests. Write a brief module mapping in the benchmark report. Run existing relevant checks. Do not replace the existing parser or request another architecture document.
2. **Freeze the downstream path:** wrap existing functions in one `extract_invoice(ocr_document, source_file)` entry point. Record parser commit/config hash. If geometry needs an extension, make one common optional interface for all adapters, then rerun all engines. Keep engine names out of parser selection logic.
3. **Build common input/adapter contracts:** rasterize PDF pages once, load original images, preserve document identity and page transforms. Separate engine serialization from common text assembly. Suggested internal types:

```python
@dataclass(frozen=True)
class OCRRegion:
    page: int
    text: str
    polygon: tuple[tuple[float, float], ...] | None
    granularity: str  # word, line, block
    confidence: float | None
    reading_order: int | None

@dataclass
class OCRDocument:
    regions: list[OCRRegion]
    page_sizes: list[tuple[int, int]]
    raw_response: object
    engine_metadata: dict

class OCRAdapter(Protocol):
    def load(self) -> None: ...
    def recognize(self, pages: Sequence[PageImage]) -> OCRDocument: ...
```

4. **Implement Tesseract first:** use `pytesseract`/CLI TSV or hOCR with boxes and confidences, separate best/fast weight directories, `--oem 1`. Development candidates: `heb+eng`, `ara+eng`, and `eng`; compare reversed language priority as configurations only on development. Start PSM 3; evaluate PSM 4/6/11 there, freeze one documented policy before validation. Do not use a global digit-only whitelist that destroys labels/vendor names. Record subprocess/load costs; confidence −1 becomes unavailable, not certainty.
5. **Implement the remaining four candidates:**

   - Hebrew PaddleOCR: use the repository's `HebrewOCR.word(...)` and `HebrewOCR.line(...)` pipelines with pinned downloaded ONNX models. Preserve words/lines, scores, and geometry. Verify the actual installed API and output with Hebrew/English/digit smoke samples; log the native recognition cascade and postprocessing as part of the configuration. Report Arabic as unsupported.
   - Kraken: pin 7.1 and the multilingual medium recognition weights plus compatible segmentation weights. Smoke-test all required scripts and structured output; preserve geometry and direction. Run CPU explicitly before optional GPU/batching configurations. Do not introduce model training into the initial pretrained comparison.
   - IronOCR: reuse the existing .NET experiment where possible. Build a local C# executable/worker accepting page inputs and returning text, regions, confidence, and metadata. Python calls it locally. Pin package/packs and record all filters. Do not use a built-in invoice-field extractor.
   - ABBYY: use the licensed local FineReader Engine SDK with a supported C#/C++ bridge as needed. Export OCR text/layout only; Python retains all business-field extraction. Verify exact build, language modules, settings, and output mapping. Missing SDK/trial/license is `BLOCKED`; retain its report row and access checklist.

   Keep dependency environments isolated as needed. All bridges follow the same internal OCR contract; bridge/startup time is included in end-to-end performance.
6. **Create truth validator/scorer:** reject extra keys/wrong types; validate dataset annotation consistency; compare with the exact rules above; include errors/timeouts/truncations in submitted-document denominators. Store canonical prediction under a separate benchmark envelope. Track pages expected/processed and extra passes.
7. **Instrument and attribute:** collect timings and process-tree memory, parser evidence, actual retries/cost, then human error review. Add meaningful fixtures for mixed RTL numbers, Eastern Arabic digits, leading zeros, long allocations, invalid printed IDs, buyer/vendor confusion, null versus zero, and later-page totals. Use fixed local response fixtures for adapter tests, including both commercial bridges; test OCR-only output mapping and failure propagation.
8. **Run all six and freeze improvements:** execute Phase 1, diagnose common parser bugs from development data, and rerun every available candidate after shared fixes. Complete blocked integrations when access becomes available. Tune only modes/models/filters within the selected six. No LLM/VLM transcription, field extraction, text repair, or seventh candidate is permitted.
9. **Evaluate routing:** use cached standalone outputs to simulate frozen decisions using primary OCR/validation evidence only. A fallback returns a fresh complete extraction through the same parser; do not merge fields ad hoc. A shared evidence-based merge can be a separately scored configuration later. Confirm actual sequential latency/compute in a live routing run; a ground-truth “pick whichever engine was correct” score is only an oracle ceiling.
10. **Produce the four requested reports:** Markdown conclusions, CSV comparison, JSON aggregate, JSONL per document. Reports include frozen config, dataset/version hashes, split, supported-script coverage, failures, denominators/intervals, cost assumptions, and trace references. Keep secrets out of reports. License keys come from environment/config and stay out of artifacts; never substitute another engine for a blocked candidate. The report must have exactly six top-level candidate groups, with modes nested or identified as configurations.

Suggested CLI contract:

```bash
python -m benchmarks.ocr inspect-repo
python -m benchmarks.ocr validate-truth --manifest benchmarks/data/manifest.jsonl
python -m benchmarks.ocr run --config benchmarks/configs/phase1.yaml --split validation
python -m benchmarks.ocr report --run-dir benchmarks/runs/<run_id>
```

Expected files: `benchmark_report.md`, `benchmark_summary.csv`, `benchmark_results.json`, `per_document_results.jsonl`. In JSONL keep `prediction` strictly canonical and all debug/performance fields alongside it. Cache keys include input hash, renderer/preprocessing, complete model configuration and versions. Cached accuracy runs do not supply latency measurements.

Required main comparison columns:

`Engine | Hebrew Accuracy | Arabic Accuracy | Digit Accuracy | Vendor Name | Business Tax ID | Invoice Number | Date | Before VAT | VAT | Total | Allocation Number | Critical Complete Accuracy | All Fields Complete Accuracy | p50 | p95 | RAM | GPU | Cost / 1M | Cost / Correct Invoice`

Define Hebrew/Arabic Accuracy as critical complete accuracy within each language group, not an undefined aggregate or `1−CER`. Publish CER/WER separately. “GPU” includes model/device, peak VRAM and hardware requirement. Unsupported rows show coverage and failures; unrun values are `NOT RUN`, never estimated accuracy. Cost columns specify page count, monthly volume, host region, applicable licenses, and elastic versus warm capacity.

Report `local_auto_accept_coverage`, `local_auto_accept_precision`, false accepts, abstentions, and correctly accepted invoices / all submissions for each standalone engine and frozen selected-engine router. These quantify demonstrated processing without an external LLM/VLM call. The remaining share is unresolved; it is not a measured LLM success rate or proof that every unresolved invoice needs an LLM.

## 9. Performance & Cost Model

Measure cold process start/model initialization separately from one-time downloads. Use persistent loaded workers for warm OCR. Record image decode/render, preprocessing, OCR, assembly, extraction/validation, and document-end-to-end wall time. For local GPU synchronize around timed work. Capture process-tree peak RSS and GPU allocator plus device VRAM; run engines separately to avoid contention.

Report p50/p95 page and document latency, failure rate, pages/sec and docs/sec on identical hardware/input dimensions. Use single-document latency and separate concurrency/batching sweeps, with **proposed** warm-up 5 documents and 3 repeated measurement passes in randomized order. Do not confuse reciprocal latency with batched throughput. Measure a workload arrival trace/peak factor before provisioning; monthly averages cannot prove interactive p95.

**Cost boundary:** this report estimates incremental OCR processing and configurable correction effort. It does not price the entire application, storage retention, authentication or databases. Include renderer/parser compute, orchestration/startup/idle time, retries, local bridge overhead, extra OCR passes, and licenses where incurred. Unpriced components stay explicit, never disappear into a claimed end-to-end total.

For documents `D`, average pages `p`, measured worker throughput `s` pages/sec and planned efficiency `u`:

```text
P = D × p                          # count actual processed/repeated pages too
worker_hours = P / (s × 3600 × u)
elastic_compute = worker_hours × hourly_rate + startup/rounding/other charges
warm_workers = ceil(P / (s × 3600 × u × month_hours))
warm_compute = warm_workers × month_hours × hourly_rate
cost_per_1000_documents = monthly_processing_cost / D × 1000
cost_per_correct_invoice = monthly_processing_cost / (D × measured_all_fields_accuracy)
```

Use sustainable measured throughput under the intended batch size, not vendor peak throughput. Avoid multiplying separate utilization penalties twice. Warm capacity needs peak-load/resilience adjustment beyond this monthly-average formula. For specialists, report cost for their applicable language volume and for the complete routed system; specialist-subset accuracy/cost cannot stand in for Hebrew-first total cost.

**License economics for the selected six:** Hebrew PaddleOCR and both Tesseract configurations have Apache-2.0 code/weights; Kraken's engine and selected medium recognizer are Apache-2.0, with the segmentation and any additional weights checked individually. Their license fee can be recorded as zero when all selected artifact rights permit the intended deployment. Compute, operations, and engineering remain costs.

IronOCR and ABBYY require the applicable commercial license/trial. Record a dated deployment-specific quote, permitted usage, developer/server/core/page limits where applicable, renewal/support fees, and the selected amortization period. IronOCR documents local operation without per-page metering; this does not imply a free library or permission for every SaaS deployment. ABBYY's exact pricing is **NOT ESTABLISHED** here. Do not insert a cloud OCR price or publish a free/trial run as its production license cost. Unresolved fees remain explicit `NOT ESTABLISHED` and prevent a definitive total-cost ranking. [S10–S15]

For each candidate, calculate scenarios at 100,000 and 1,000,000 documents/month with 1, 2, and 3 pages/document, using measured throughput and applicable licensing. Include both elastic and continuously warm workers. Separate one-time engineering/license charges from monthly recurring charges and show the amortization policy.

**Self-hosted capacity scenarios—NOT measured engine costs:** CPU example uses AWS Fargate Linux/x86 US East (N. Virginia), on-demand, 4 vCPU/16 GB: official example rates $0.000011244/vCPU-second and $0.000001235/GB-second, giving **$0.2330496/hour**. [S16] Proposed CPU throughput is **0.5 pages/sec for the whole worker**, `u=0.70`, a 30-day/720-hour month. GPU example assumes a compatible host at **$1/hour and 2 pages/sec**, same efficiency/month. GPU rate and both throughputs are **unverified scenario assumptions**, not quotes or benchmark predictions; replace them with measured hardware and a dated regional quote. Fargate is the CPU example, not GPU hosting.

| Docs/month | Pages/doc | CPU elastic compute | CPU warm compute | GPU elastic compute* | GPU warm compute* |
|---:|---:|---:|---:|---:|---:|
| 100,000 | 1 | $18.50 | $167.80 | $19.84 | $720 |
| 100,000 | 2 | $36.99 | $167.80 | $39.68 | $720 |
| 100,000 | 3 | $55.49 | $167.80 | $59.52 | $720 |
| 1,000,000 | 1 | $184.96 | $335.59 | $198.41 | $720 |
| 1,000,000 | 2 | $369.92 | $503.39 | $396.83 | $720 |
| 1,000,000 | 3 | $554.88 | $671.18 | $595.24 | $720 |

\*Assumed GPU rate. Elastic figures are idealized throughput-based active capacity before task minimums/startup, burst idle time and additional charges. Warm figures retain enough whole workers for monthly average demand, without resilience/peak provision. Renderer/parser time must be included in measured throughput or added separately. Fargate billing runs from image pull/start through termination and includes minimum durations; record actual task seconds. License cost is zero only where rights permit it, not automatically for every downloadable model.

At 1M docs, CPU elastic cost/1k docs is **$0.185/$0.370/$0.555** for 1/2/3 pages; CPU warm is **$0.336/$0.503/$0.671**. GPU elastic is **$0.198/$0.397/$0.595**, warm **$0.720**. These are calculated examples; every candidate report must recompute its own six scenarios. A CPU worker achieving only 0.1 pages/sec instead of 0.5 multiplies active hours by five; never publish the example as a measured cost for any selected engine.

**Correctness and corrections:** use all-fields complete accuracy for the principal cost/correct denominator; report critical-complete cost/correct alongside it. When accuracy is zero, result is infinity; when not measured, **NOT ESTABLISHED**. For illustration only, $335.59 monthly processing cost and 75% all-fields correctness over 1M invoices gives `$335.59/750000 = $0.00044745` per automatically correct invoice. Both inputs are scenario assumptions; this does not price the incorrect invoices or omitted license/engineering costs.

Add measured correction economics:

```text
correction_cost = number_reviewed × mean_review_seconds / 3600 × loaded_hourly_wage
effective_cost_per_submission = (processing + correction_cost + applicable_engineering/licensing) / D
```

For illustration only, reviewing 10% of 1M documents for 30 sec at $10/hour costs **$8,333.33**. Review fraction is not necessarily `1−accuracy`; include false accepts, audits and unreadable uploads. User correction effort also has a cost even if the startup does not pay wages. Measure time and error detection rather than assigning arbitrary dollar savings.

**Local fallback economics:** `C_total = C_primary(all processed pages) + C_selected_local_fallback(actual routed pages) + routing/duplicate preprocessing/retries + applicable licenses`. Only the selected six may supply fallback OCR. Measure fallback correctness on routed hard documents, rather than applying standalone average accuracy to them. Report routed document/page rates, primary errors corrected, remaining silent errors, and actual sequential p95. A validation-only router can miss plausible wrong values. No external LLM/VLM call or API price is included; demonstrated local coverage is the benchmark outcome.

## 10. Final Action Plan

| Decision | Required action |
|---|---|
| Fixed benchmark scope | Exactly six: RivoksLab/paddleocr-hebrew; Tesseract + tessdata_best; Tesseract + tessdata_fast; Kraken; IronOCR; ABBYY FineReader Engine. No additions or substitutions. |
| Initial implementation | Freeze the shared parser and schema; implement best/fast, Hebrew PaddleOCR, and Kraken; prepare both commercial local bridges during Phase 1. |
| Required completion | Attempt and report all six. Resolve commercial access blockers; an incomplete six-candidate run must be labeled incomplete. |
| Hebrew question | Compare exact invoice fields on real Israeli phone photos, mixed Hebrew/English/digits, long allocation numbers, glare, skew, and thermal receipts. Hebrew Paddle word/line modes belong to one candidate. |
| Arabic/English question | Compare documented configurations within the same six. Show the Hebrew specialist's unsupported Arabic coverage; test a frozen router to a selected multilingual engine if useful. |
| Local processing question | Measure correct automatic acceptance without external LLM/VLM calls, including abstentions and silent errors. Confidence/checksum/amount consistency alone cannot certify correctness. |
| Commercial value question | Do IronOCR's integration/preprocessing or ABBYY's SDK capabilities improve complete-field accuracy/correction effort enough to justify actual license costs? |
| Final selection | Use untouched holdout accuracy, language/phone-photo slices, uncertainty, measured latency/compute, license feasibility, and correction effort. No winner is established by research alone. |

Required delivery: reproducible configurations and four reports covering exactly six candidate groups. Word/line/model-size/filter variants appear underneath their candidate. Keep absent, unsupported, blocked, failed, and unrun outcomes explicit. Benchmark results remain unmeasured until this plan is executed.

## 11. Sources

Primary sources checked 2026-10-07. References establish implementation targets, documented capabilities, licenses, and cost assumptions; they do not establish invoice-field accuracy. Freeze actual installed revisions before running.

- **S1:** [RivoksLab/paddleocr-hebrew repository](https://github.com/RivoksLab/paddleocr-hebrew): pipeline, word/line modes, inference models, scope, license, and project-reported evaluation caveats.
- **S2:** [Rivok/paddleocr-hebrew model repository](https://huggingface.co/Rivok/paddleocr-hebrew): downloadable ONNX artifacts and Apache-2.0 model card.
- **S3:** [Tesseract releases](https://github.com/tesseract-ocr/tesseract/releases): 5.5.3 target.
- **S4:** [Tesseract data-file documentation](https://tesseract-ocr.github.io/tessdoc/Data-Files.html): language models and LSTM requirements.
- **S5:** [tessdata_best](https://github.com/tesseract-ocr/tessdata_best): Hebrew/Arabic/English artifacts and license.
- **S6:** [tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast): faster artifacts and license.
- **S7:** [Kraken repository](https://github.com/mittagessen/kraken): local execution, RTL/structured output, engine license.
- **S8:** [Kraken releases](https://github.com/mittagessen/kraken/releases): 7.1 multilingual recognition models, Hebrew/Arabic/English project evaluation, historical training domain, and current CLI/API changes.
- **S9:** [Kraken multilingual medium recognition model](https://zenodo.org/records/21788410): selected weights, model description, and Apache-2.0 license. Pin the segmentation artifact separately.
- **S10:** [IronOCR official technical overview](https://ironsoftware.com/ocr/csharp/ai-info/): local .NET execution, Tesseract lineage, languages, structured output, preprocessing, and no per-page metering.
- **S11:** [IronOCR Hebrew documentation](https://ironsoftware.com/ocr/csharp/languages/hebrew/): Hebrew language-pack configurations.
- **S12:** [IronOCR licensing](https://ironsoftware.com/ocr/csharp/licensing/): resolve the applicable commercial deployment rights and quote.
- **S13:** [ABBYY FineReader Engine](https://www.abbyy.com/ocr-sdk/): selected local SDK product.
- **S14:** [ABBYY recognition technologies/languages](https://www.abbyy.com/ocr-sdk/features/ocr/): documented recognition-language capabilities; verify required licensed modules.
- **S15:** [ABBYY image processing](https://www.abbyy.com/ocr-sdk/features/image-processing/): camera-image preprocessing capabilities, to be benchmarked on our photos.
- **S16:** [AWS Fargate pricing](https://aws.amazon.com/fargate/pricing/): CPU hosting example only, not an OCR candidate. GPU hourly rate and both throughput assumptions are unverified scenarios.
