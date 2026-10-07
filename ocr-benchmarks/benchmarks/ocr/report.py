"""Report generation (roadmap §8.10).

Reads a completed run directory (`run_manifest.json` + `results.jsonl`) and a
manifest with ground truth, scores every (document, engine) row, and writes
the four required artifacts:

- `benchmark_report.md`      — human-readable summary with the §8.10 column set
- `benchmark_summary.csv`    — one row per engine
- `benchmark_results.json`   — aggregate JSON (counts, intervals, denominators)
- `per_document_results.jsonl` — one line per (document, engine) with the
  strictly-canonical prediction + scoring details alongside

Every candidate's status appears (ok / BLOCKED / errored). The report keeps
six top-level candidate groups; configurations nest underneath.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from benchmarks.ocr.manifest import ManifestEntry, load_manifest
from benchmarks.ocr.scorer import (
    AMOUNT_FIELDS,
    BUSINESS_FIELDS,
    CRITICAL_FIELDS,
    DocumentScore,
    EngineAggregate,
    aggregate,
    score_row,
    wilson_interval,
)


# Candidate # (roadmap §4). Keeps the six top-level candidate groups.
# Tesseract shares one adapter but splits into two candidates by tessdata.
ADAPTER_TO_CANDIDATE: dict[str, tuple[int, str]] = {
    "paddleocr-hebrew": (1, "RivoksLab/paddleocr-hebrew"),
    "tesseract": (2, "Tesseract + tessdata_best"),  # overridden below for fast
    "kraken": (4, "Kraken"),
    "ironocr": (5, "IronOCR"),
    "abbyy": (6, "ABBYY FineReader Engine"),
}


def _candidate_for_engine(adapter: str, engine_id: str, params: dict[str, Any]) -> tuple[int, str]:
    if adapter == "tesseract":
        tessdata = str(params.get("tessdata_dir") or "")
        if "fast" in engine_id.lower() or "fast" in tessdata.lower():
            return (3, "Tesseract + tessdata_fast")
        return (2, "Tesseract + tessdata_best")
    return ADAPTER_TO_CANDIDATE.get(adapter, (0, adapter))


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_results(run_dir: Path) -> list[dict[str, Any]]:
    results_path = run_dir / "results.jsonl"
    rows: list[dict[str, Any]] = []
    with results_path.open(encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            rows.append(json.loads(raw))
    return rows


def _load_run_manifest(run_dir: Path) -> dict[str, Any]:
    with (run_dir / "run_manifest.json").open(encoding="utf-8") as f:
        return json.load(f)


def _manifest_from_run(run_manifest: dict[str, Any], manifest_override: Path | None) -> Path:
    if manifest_override:
        return manifest_override.resolve()
    # The run.py env snapshot captures cwd, not the manifest path. The config
    # from which the run was launched contains the (possibly relative) path.
    # We can't recover it reliably here, so require the caller to pass it in.
    raise ValueError(
        "manifest path required — pass --manifest when invoking `report`"
    )


def _truth_by_document(manifest: Iterable[ManifestEntry]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for entry in manifest:
        if entry.ground_truth is None and entry.annotation is None:
            continue
        out[entry.document_id] = {
            "ground_truth": entry.ground_truth or {},
            "annotation": entry.annotation or {},
        }
    return out


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def _candidate_of(adapter: str, engine_id: str = "", params: dict[str, Any] | None = None) -> tuple[int, str]:
    return _candidate_for_engine(adapter, engine_id, params or {})


def _engine_params(run_manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["id"]: e for e in run_manifest.get("engines", [])}


def _ratio(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return num / den


def _fmt_pct(num: int, den: int) -> str:
    if den <= 0:
        return "n/a"
    r = num / den
    lo, hi = wilson_interval(num, den)
    return f"{r*100:5.1f}%  [{lo*100:4.1f}%, {hi*100:4.1f}%]  ({num}/{den})"


def _digit_accuracy(agg: EngineAggregate) -> str:
    if agg.digit_accuracy_n == 0:
        return "n/a"
    return f"{(agg.digit_accuracy_sum / agg.digit_accuracy_n) * 100:5.1f}%"


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def _render_markdown(
    run_manifest: dict[str, Any],
    engines: dict[str, dict[str, Any]],
    aggs: dict[str, EngineAggregate],
    scored_doc_count: int,
) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines: list[str] = []
    lines.append("# Malio OCR benchmark — report")
    lines.append("")
    lines.append(f"- Generated: {now}")
    lines.append(f"- Run id: `{run_manifest.get('run_id')}`")
    lines.append(f"- Documents scored: {scored_doc_count}")
    lines.append(f"- Engines executed: {len(engines)}")
    lines.append("")
    lines.append("## §8.10 main comparison")
    lines.append("")
    lines.append(
        "| Candidate | Engine config | Status | Critical Complete | All Fields Complete | "
        "Digit Acc | Vendor | Tax ID | Invoice # | Date | Before VAT | VAT | Total | Allocation # |"
    )
    lines.append(
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    )

    # Group by candidate # (roadmap §4) so we always print six top-level groups.
    by_candidate: dict[int, list[str]] = {}
    for eid, spec in engines.items():
        n, _name = _candidate_of(spec.get("adapter", "unknown"), eid, spec.get("params"))
        by_candidate.setdefault(n, []).append(eid)

    for cand_n in sorted(by_candidate):
        for eid in by_candidate[cand_n]:
            spec = engines[eid]
            adapter = spec.get("adapter", "unknown")
            _, cand_name = _candidate_of(adapter, eid, spec.get("params"))
            agg = aggs.get(eid)
            if agg is None:
                lines.append(
                    f"| {cand_n}. {cand_name} | `{eid}` | NOT RUN | — | — | — | — | — | — | — | — | — | — | — |"
                )
                continue
            status = _engine_status(agg)
            lines.append(
                "| {cand}. {name} | `{eid}` | {status} | {crit} | {allf} | {dig} | "
                "{vendor} | {tax} | {inv} | {date} | {bvat} | {vat} | {total} | {alloc} |".format(
                    cand=cand_n,
                    name=cand_name,
                    eid=eid,
                    status=status,
                    crit=_fmt_pct(agg.critical_complete_ok, agg.critical_complete_total),
                    allf=_fmt_pct(agg.all_fields_complete_ok, agg.all_fields_complete_total),
                    dig=_digit_accuracy(agg),
                    vendor=_fmt_pct(agg.per_field_present_correct.get("vendor_name", 0), agg.per_field_present_total.get("vendor_name", 0)),
                    tax=_fmt_pct(agg.per_field_present_correct.get("business_tax_id", 0), agg.per_field_present_total.get("business_tax_id", 0)),
                    inv=_fmt_pct(agg.per_field_present_correct.get("invoice_number", 0), agg.per_field_present_total.get("invoice_number", 0)),
                    date=_fmt_pct(agg.per_field_present_correct.get("invoice_date", 0), agg.per_field_present_total.get("invoice_date", 0)),
                    bvat=_fmt_pct(agg.per_field_present_correct.get("amount_before_vat", 0), agg.per_field_present_total.get("amount_before_vat", 0)),
                    vat=_fmt_pct(agg.per_field_present_correct.get("vat_amount", 0), agg.per_field_present_total.get("vat_amount", 0)),
                    total=_fmt_pct(agg.per_field_present_correct.get("total_amount", 0), agg.per_field_present_total.get("total_amount", 0)),
                    alloc=_fmt_pct(agg.per_field_present_correct.get("allocation_number", 0), agg.per_field_present_total.get("allocation_number", 0)),
                )
            )

    lines.append("")
    lines.append("## Status summary")
    lines.append("")
    lines.append("| Engine | Submitted | OK | BLOCKED | Errored | Dominant error labels |")
    lines.append("|---|---:|---:|---:|---:|---|")
    for eid, agg in aggs.items():
        labels = ", ".join(f"{k}×{v}" for k, v in sorted(agg.error_labels.items(), key=lambda kv: -kv[1])[:3])
        lines.append(
            f"| `{eid}` | {agg.submitted} | {agg.ok} | {agg.blocked} | {agg.errored} | {labels or '—'} |"
        )

    lines.append("")
    lines.append("## Required-but-not-measured columns (§8.10)")
    lines.append("")
    lines.append(
        "Latency p50/p95, RAM, GPU VRAM, Cost/1M, and Cost/Correct Invoice depend on "
        "workload measurements, hardware specs, dated license quotes, and warm vs. "
        "elastic capacity assumptions (§9). They remain **NOT ESTABLISHED** here; the "
        "scoring harness does not infer them from timings alone."
    )
    lines.append("")
    lines.append("## BLOCKED candidates — access checklist")
    lines.append("")
    load_errors = run_manifest.get("load_errors") or {}
    if not load_errors:
        lines.append("_No BLOCKED candidates in this run._")
    else:
        for eid, msg in load_errors.items():
            lines.append(f"- **`{eid}`** — {msg}")
    lines.append("")
    return "\n".join(lines)


def _engine_status(agg: EngineAggregate) -> str:
    if agg.blocked == agg.submitted and agg.submitted > 0:
        return "BLOCKED"
    if agg.ok == 0 and agg.errored > 0:
        return "ERRORED"
    if agg.blocked > 0 and agg.ok > 0:
        return "PARTIAL"
    if agg.ok > 0:
        return "OK"
    return "NOT RUN"


# ---------------------------------------------------------------------------
# CSV + JSON
# ---------------------------------------------------------------------------

CSV_FIELDS = [
    "candidate_number",
    "candidate_name",
    "engine_id",
    "adapter",
    "status",
    "submitted",
    "ok",
    "blocked",
    "errored",
    "critical_complete_ok",
    "critical_complete_total",
    "critical_complete_rate",
    "all_fields_complete_ok",
    "all_fields_complete_total",
    "all_fields_complete_rate",
    "digit_accuracy_mean",
    "digit_accuracy_samples",
    *(f"present_{f}_correct" for f in BUSINESS_FIELDS),
    *(f"present_{f}_total" for f in BUSINESS_FIELDS),
    *(f"absent_{f}_correct" for f in BUSINESS_FIELDS),
    *(f"absent_{f}_total" for f in BUSINESS_FIELDS),
]


def _write_csv(path: Path, engines: dict[str, dict[str, Any]], aggs: dict[str, EngineAggregate]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for eid, spec in engines.items():
            adapter = spec.get("adapter", "unknown")
            cand_n, cand_name = _candidate_of(adapter, eid, spec.get("params"))
            agg = aggs.get(eid)
            if agg is None:
                writer.writerow(
                    {
                        "candidate_number": cand_n,
                        "candidate_name": cand_name,
                        "engine_id": eid,
                        "adapter": adapter,
                        "status": "NOT RUN",
                        "submitted": 0,
                        "ok": 0,
                        "blocked": 0,
                        "errored": 0,
                    }
                )
                continue
            row: dict[str, Any] = {
                "candidate_number": cand_n,
                "candidate_name": cand_name,
                "engine_id": eid,
                "adapter": adapter,
                "status": _engine_status(agg),
                "submitted": agg.submitted,
                "ok": agg.ok,
                "blocked": agg.blocked,
                "errored": agg.errored,
                "critical_complete_ok": agg.critical_complete_ok,
                "critical_complete_total": agg.critical_complete_total,
                "critical_complete_rate": _ratio(agg.critical_complete_ok, agg.critical_complete_total),
                "all_fields_complete_ok": agg.all_fields_complete_ok,
                "all_fields_complete_total": agg.all_fields_complete_total,
                "all_fields_complete_rate": _ratio(agg.all_fields_complete_ok, agg.all_fields_complete_total),
                "digit_accuracy_mean": (
                    agg.digit_accuracy_sum / agg.digit_accuracy_n if agg.digit_accuracy_n else None
                ),
                "digit_accuracy_samples": agg.digit_accuracy_n,
            }
            for f_name in BUSINESS_FIELDS:
                row[f"present_{f_name}_correct"] = agg.per_field_present_correct.get(f_name, 0)
                row[f"present_{f_name}_total"] = agg.per_field_present_total.get(f_name, 0)
                row[f"absent_{f_name}_correct"] = agg.per_field_absent_correct.get(f_name, 0)
                row[f"absent_{f_name}_total"] = agg.per_field_absent_total.get(f_name, 0)
            writer.writerow(row)


def _write_json(path: Path, run_manifest: dict[str, Any], engines: dict[str, dict[str, Any]], aggs: dict[str, EngineAggregate], scored_docs: int) -> None:
    doc: dict[str, Any] = {
        "run_id": run_manifest.get("run_id"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "documents_scored": scored_docs,
        "engines": [],
    }
    for eid, spec in engines.items():
        adapter = spec.get("adapter", "unknown")
        cand_n, cand_name = _candidate_of(adapter)
        agg = aggs.get(eid)
        entry: dict[str, Any] = {
            "candidate_number": cand_n,
            "candidate_name": cand_name,
            "engine_id": eid,
            "adapter": adapter,
            "params": spec.get("params", {}),
            "status": _engine_status(agg) if agg else "NOT RUN",
        }
        if agg:
            entry["counts"] = {
                "submitted": agg.submitted,
                "ok": agg.ok,
                "blocked": agg.blocked,
                "errored": agg.errored,
            }
            entry["critical_complete"] = {
                "correct": agg.critical_complete_ok,
                "total": agg.critical_complete_total,
                "wilson_95": wilson_interval(agg.critical_complete_ok, agg.critical_complete_total),
            }
            entry["all_fields_complete"] = {
                "correct": agg.all_fields_complete_ok,
                "total": agg.all_fields_complete_total,
                "wilson_95": wilson_interval(agg.all_fields_complete_ok, agg.all_fields_complete_total),
            }
            entry["digit_accuracy_mean"] = (
                agg.digit_accuracy_sum / agg.digit_accuracy_n if agg.digit_accuracy_n else None
            )
            entry["per_field_present"] = {
                f_name: {
                    "correct": agg.per_field_present_correct.get(f_name, 0),
                    "total": agg.per_field_present_total.get(f_name, 0),
                    "wilson_95": wilson_interval(
                        agg.per_field_present_correct.get(f_name, 0),
                        agg.per_field_present_total.get(f_name, 0),
                    ),
                }
                for f_name in BUSINESS_FIELDS
            }
            entry["per_field_absent"] = {
                f_name: {
                    "correct": agg.per_field_absent_correct.get(f_name, 0),
                    "total": agg.per_field_absent_total.get(f_name, 0),
                }
                for f_name in BUSINESS_FIELDS
            }
            entry["error_labels"] = agg.error_labels
        doc["engines"].append(entry)
    doc["load_errors"] = run_manifest.get("load_errors") or {}
    with path.open("w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)


def _write_per_document_jsonl(path: Path, scores: list[DocumentScore], rows_by_key: dict[tuple[str, str], dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for s in scores:
            row = rows_by_key.get((s.document_id, s.engine_id), {})
            out: dict[str, Any] = {
                "document_id": s.document_id,
                "engine_id": s.engine_id,
                "status": s.status,
                "prediction": row.get("prediction"),
                "scoring": {
                    "critical_complete": s.critical_complete,
                    "all_fields_complete": s.all_fields_complete,
                    "digit_accuracy": s.digit_accuracy,
                    "reference_digit_count": s.reference_digit_count,
                    "fields": [
                        {
                            "field": o.field,
                            "presence": o.presence,
                            "scorable": o.scorable,
                            "predicted": _json_safe(o.predicted),
                            "expected": _json_safe(o.expected),
                            "correct": o.correct,
                            "error_label": o.error_label,
                        }
                        for o in s.outcomes
                    ],
                },
                "engine_metadata": row.get("engine_metadata"),
                "timings_ms": row.get("timings_ms"),
                "error": row.get("error"),
            }
            f.write(json.dumps(out, ensure_ascii=False) + "\n")


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def generate_report(run_dir: Path, manifest_path: Path, out_dir: Path | None = None) -> Path:
    run_dir = run_dir.resolve()
    out_dir = (out_dir or run_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _load_results(run_dir)
    run_manifest = _load_run_manifest(run_dir)
    engines = _engine_params(run_manifest)
    manifest_entries = load_manifest(manifest_path)
    truth = _truth_by_document(manifest_entries)

    scores: list[DocumentScore] = []
    rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        doc_truth = truth.get(row.get("document_id"))
        s = score_row(row, doc_truth)
        scores.append(s)
        rows_by_key[(s.document_id, s.engine_id)] = row

    aggs = aggregate(scores)
    scored_docs = len({s.document_id for s in scores if s.status == "ok"})

    md_path = out_dir / "benchmark_report.md"
    md_path.write_text(_render_markdown(run_manifest, engines, aggs, scored_docs), encoding="utf-8")

    _write_csv(out_dir / "benchmark_summary.csv", engines, aggs)
    _write_json(out_dir / "benchmark_results.json", run_manifest, engines, aggs, scored_docs)
    _write_per_document_jsonl(out_dir / "per_document_results.jsonl", scores, rows_by_key)
    return out_dir
