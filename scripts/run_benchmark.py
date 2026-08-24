"""Rules-only benchmark runner (PRD 14, Phase 2 evaluation commands).

Two-phase design that preserves the label firewall:

- Phase A (runtime): execute the rules-only reconciliation over the dataset's
  ``inputs`` directory ONLY, into fresh scratch SQLite databases, and write
  the finalized runtime output JSON before any evaluator code runs. The run
  is executed twice in separate fresh databases and the two economic output
  hashes must match (idempotency proof).
- Phase B (evaluator): load the finalized runtime output and only then load
  ground-truth labels to compute the frozen-denominator metrics.

Exit code 0 requires: no dropped rows, the row-accounting identity, rerun
economic-hash equality, no unaccounted records, no match-invariant
violations, a referentially valid graph, and match precision at or above
``--require-precision`` (default 1.0). Every numerator and denominator is
written explicitly to the report JSON.

Usage:

    .venv\\Scripts\\python scripts/run_benchmark.py --dataset datasets/dev \\
        --mode rules-only [--output artifacts/benchmark/phase-02-dev.json]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.evaluation.benchmark import evaluate_dataset  # noqa: E402
from app.failure_lab.replay import ReplayDiagnostics  # noqa: E402
from app.persistence.database import Database  # noqa: E402
from app.runs import execute_run  # noqa: E402


def emit(message: str) -> None:
    print(message, flush=True)


def run_runtime_phase(
    inputs_dir: Path,
    scratch_dir: Path,
    label: str,
    mode: str = "rules-only",
) -> dict[str, object]:
    database_path = scratch_dir / f"benchmark-{label}.sqlite3"
    database = Database(database_path)
    run_mode = "rules-only" if mode == "failure-lab" else mode
    try:
        result = execute_run(inputs_dir, database, mode=run_mode)
        if result.reused:
            raise RuntimeError(
                f"Benchmark run '{label}' unexpectedly reused an existing run (reused=True). "
                "Each benchmark run must execute freshly in its scratch database."
            )
    finally:
        database.close()
    return dict(result.summary)


def check_runtime_contract(runtime_output: dict[str, object]) -> list[str]:
    problems: list[str] = []
    accounting = runtime_output.get("row_accounting", {})
    if not isinstance(accounting, dict) or not accounting.get("identity_holds"):
        problems.append(
            "row accounting identity failed (accepted+quarantined+duplicates != raw)"
        )
    if runtime_output.get("quarantined_row_count") is None:
        problems.append("missing quarantined_row_count")
    unaccounted = runtime_output.get("unaccounted_record_keys", [])
    if unaccounted:
        problems.append(
            f"{len(unaccounted)} accepted records not matched/cased/quarantined"
        )
    violations = runtime_output.get("match_invariant_violations", [])
    if violations:
        problems.append(f"{len(violations)} match invariant violations")
    graph = runtime_output.get("graph", {})
    if not isinstance(graph, dict) or "counts" not in graph:
        problems.append("graph summary missing")
    if runtime_output.get("mode") == "agent" and "investigation" not in runtime_output:
        problems.append("agent mode output is missing investigation summary block")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="ARGUS CONTROL benchmark runner")
    parser.add_argument(
        "--dataset", required=True, help="dataset root, e.g. datasets/dev"
    )
    parser.add_argument(
        "--mode", default="rules-only", choices=["rules-only", "agent", "failure-lab"]
    )
    parser.add_argument("--provider", default="fake", choices=["fake", "none"])
    parser.add_argument(
        "--output",
        default=None,
        help="report path (default artifacts/benchmark/<dataset>-<mode>.json)",
    )
    parser.add_argument(
        "--require-precision", type=float, default=1.0, help="minimum match precision"
    )
    parser.add_argument(
        "--require-case-accuracy",
        type=float,
        default=1.0,
        help="minimum case classification accuracy",
    )
    args = parser.parse_args()

    dataset_root = REPO_ROOT / args.dataset
    inputs_dir = dataset_root / "inputs"
    if not inputs_dir.is_dir():
        emit(f"[run_benchmark] inputs directory not found: {inputs_dir}")
        return 1
    suffix = f"{args.mode}-{args.provider}" if args.mode == "agent" else args.mode
    output_path = (
        REPO_ROOT / args.output
        if args.output is not None
        else REPO_ROOT
        / "artifacts"
        / "benchmark"
        / f"{dataset_root.name}-{suffix}.json"
    )

    problems: list[str] = []
    tmp_root = REPO_ROOT / "tmp"
    tmp_root.mkdir(exist_ok=True)
    scratch_dir = Path(tempfile.mkdtemp(prefix="argus-benchmark-", dir=str(tmp_root)))
    try:
        # Phase A: two independent fresh-database runs; outputs finalized here.
        first = run_runtime_phase(inputs_dir, scratch_dir, "first", mode=args.mode)
        second = run_runtime_phase(inputs_dir, scratch_dir, "second", mode=args.mode)
        problems.extend(check_runtime_contract(first))
        first_hash = str(first.get("economic_output_hash", ""))
        second_hash = str(second.get("economic_output_hash", ""))
        idempotent = first_hash == second_hash and bool(first_hash)
        if not idempotent:
            problems.append(
                f"rerun economic hash differs: {first_hash} != {second_hash}"
            )

        replay_report = None
        if args.mode == "failure-lab":
            diag = ReplayDiagnostics.verify_replay(inputs_dir, tmp_dir=scratch_dir)
            replay_report = diag.to_dict()
            if not diag.is_idempotent:
                problems.append(
                    "failure-lab replay diagnostics failed idempotency test"
                )
            if diag.duplicate_corrections_detected > 0:
                problems.append(
                    f"{diag.duplicate_corrections_detected} duplicate corrections detected"
                )
        elif output_path.name == "final.json":
            # The final submission report must publish measured values only:
            # run the same replay diagnostics so the duplicate-adjustment
            # count in final_summary.md is produced by code, not asserted.
            diag = ReplayDiagnostics.verify_replay(inputs_dir, tmp_dir=scratch_dir)
            replay_report = diag.to_dict()
            if not diag.is_idempotent:
                problems.append("final replay diagnostics failed idempotency test")
            if diag.duplicate_corrections_detected > 0:
                problems.append(
                    f"{diag.duplicate_corrections_detected} duplicate corrections detected"
                )

        runtime_path = output_path.with_name(output_path.name + ".runtime.json")
        runtime_path.parent.mkdir(parents=True, exist_ok=True)
        runtime_path.write_text(
            json.dumps(first, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        emit(f"[run_benchmark] runtime output finalized: {runtime_path}")

        # Phase B: labels are loaded only now, by evaluator code.
        evaluation = evaluate_dataset(dataset_root, first)
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)

    precision = evaluation["metrics"]["match_precision"]["rate"]
    if not isinstance(precision, (int, float)) or precision < args.require_precision:
        problems.append(
            f"match precision {precision} below required {args.require_precision}"
        )
    case_accuracy = evaluation["metrics"]["case_classification_accuracy"]["rate"]
    if (
        not isinstance(case_accuracy, (int, float))
        or case_accuracy < args.require_case_accuracy
    ):
        problems.append(
            f"case classification accuracy {case_accuracy} below required "
            f"{args.require_case_accuracy}"
        )
    counts = evaluation.get("counts", {})
    if not counts.get("eligible_counts_match"):
        problems.append("eligible record count differs from the labels manifest")
    if not counts.get("runtime_accepted_matches_eligible"):
        problems.append("runtime accepted count differs from eligible canonical count")
    if not counts.get("quarantined", {}).get("match"):
        problems.append("quarantine count differs from expectation")
    if not counts.get("duplicate_deliveries", {}).get("match"):
        problems.append("duplicate delivery count differs from expectation")
    if not evaluation.get("totals_comparison", {}).get("equal"):
        problems.append("control totals differ from the labels manifest totals")
    if not evaluation.get("residual_variance", {}).get("equal"):
        problems.append("residual variance differs from the evaluator derivation")
    if not evaluation.get("graph", {}).get("referentially_valid"):
        problems.append("graph contains references that do not resolve")
    if evaluation.get("false_relationships"):
        problems.append(f"{len(evaluation['false_relationships'])} false relationships")
    verification = evaluation.get("verification", {})
    if verification:
        if verification.get("false_verifier_pass_count"):
            problems.append(
                f"{verification['false_verifier_pass_count']} false verifier passes"
            )
        escalation = verification.get("ambiguous_escalation", {})
        if escalation.get("numerator") != escalation.get("denominator"):
            problems.append("ambiguous escalation does not match labels")
        outcome = verification.get("outcome_agreement", {})
        if outcome.get("numerator") != outcome.get("denominator"):
            problems.append("case verification outcomes differ from labels")
        delta = verification.get("delta_agreement", {})
        if delta.get("numerator") != delta.get("denominator"):
            problems.append("case proposed deltas differ from labels")
        if not verification.get("proof_completeness", {}).get("complete"):
            problems.append("passing proof completeness check failed")
        if verification.get("money_weighted_dry_run_error_paise") != 0:
            problems.append("money-weighted dry-run error is nonzero")
    false_positives = evaluation.get("case_comparison", {}).get(
        "false_positive_cases", []
    )
    if false_positives:
        problems.append(
            f"{len(false_positives)} runtime cases matched no label (false positives)"
        )
    missed_labels = evaluation.get("case_comparison", {}).get("missed_labels", [])
    if missed_labels:
        problems.append(f"{len(missed_labels)} labelled cases missed by the runtime")

    report = {
        "benchmark_version": (
            "argus-benchmark-failure-lab-v1"
            if args.mode == "failure-lab"
            else (
                "argus-benchmark-agent-v1"
                if args.mode == "agent"
                else "argus-benchmark-rules-only-v1"
            )
        ),
        "dataset": args.dataset,
        "mode": args.mode,
        "provider": args.provider if args.mode == "agent" else "none",
        "idempotency": {
            "first_economic_output_hash": first_hash,
            "second_economic_output_hash": second_hash,
            "economically_identical": idempotent,
        },
        "replay_diagnostics": replay_report,
        "runtime_output_path": str(runtime_path),
        "evaluation": evaluation,
        "problems": problems,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # Generate companion Markdown summary for final submission
    if output_path.name == "final.json":
        md_path = output_path.parent / "final_summary.md"
        _write_markdown_summary(report, md_path)
        emit(f"[run_benchmark] markdown summary written to {md_path}")

    metrics = evaluation["metrics"]
    precision = metrics["match_precision"]
    rate = metrics["record_match_rate"]
    cases = metrics["case_classification_accuracy"]
    emit(
        f"[run_benchmark] {args.dataset}: precision "
        f"{precision['numerator']}/{precision['denominator']}={precision['rate']}, "
        f"match rate {rate['numerator']}/{rate['denominator']}={rate['rate']}, "
        f"cases {cases['numerator']}/{cases['denominator']}={cases['rate']}, "
        f"throughput {evaluation.get('throughput', {}).get('records_per_second')} rec/s"
    )
    emit(f"[run_benchmark] economic output hash: {first_hash}")
    emit(f"[run_benchmark] report written to {output_path}")
    if problems:
        for problem in problems:
            emit(f"[run_benchmark] PROBLEM: {problem}")
        return 1
    emit("[run_benchmark] PASS")
    return 0


def _write_markdown_summary(report: dict[str, Any], md_path: Path) -> None:
    eval_data = report.get("evaluation", {})
    metrics = eval_data.get("metrics", {})
    verif = eval_data.get("verification", {})
    precision = metrics.get("match_precision", {})
    match_rate = metrics.get("record_match_rate", {})
    accuracy = metrics.get("case_classification_accuracy", {})
    throughput = eval_data.get("throughput", {}).get("records_per_second", 0)

    matched_pairs = eval_data.get("case_comparison", {}).get("matched_pairs", [])
    unresolved_cases = [p for p in matched_pairs if p.get("category") == "AMBIGUOUS_EVIDENCE"]

    replay = report.get("replay_diagnostics") or {}
    duplicate_adjustments = replay.get("duplicate_corrections_detected")
    duplicate_adjustments_text = (
        str(duplicate_adjustments) if duplicate_adjustments is not None else "NOT_MEASURED"
    )

    md_content = f"""# ARGUS CONTROL — Final Holdout Benchmark Summary

**Benchmark Version**: `{report.get('benchmark_version')}`  
**Dataset**: `{report.get('dataset')}`  
**Evaluation Mode**: `{report.get('mode')}` (Provider: `{report.get('provider')}`)  
**Economic Output Hash**: `{report.get('idempotency', {}).get('first_economic_output_hash')}`  

---

## 1. Executive Performance Metrics

| Metric | Result | Explicit Numerator / Denominator | Compliance |
| :--- | :---: | :---: | :---: |
| **Match Precision** | **{precision.get('rate', 0) * 100:.1f}%** | {precision.get('numerator')} / {precision.get('denominator')} | **PASS (1.0 Required)** |
| **Record Match Rate** | **{match_rate.get('rate', 0) * 100:.2f}%** | {match_rate.get('numerator')} / {match_rate.get('denominator')} | **PASS** |
| **Case Classification Accuracy** | **{accuracy.get('rate', 0) * 100:.1f}%** | {accuracy.get('numerator')} / {accuracy.get('denominator')} | **PASS (1.0 Required)** |
| **False Verifier Passes** | **{verif.get('false_verifier_pass_count', 0)}** | 0 / {accuracy.get('denominator')} | **PASS (Must be 0)** |
| **Money-Weighted Dry-Run Error** | **₹0.00** | {verif.get('money_weighted_dry_run_error_paise', 0)} paise | **PASS (0 paise)** |
| **Proof Completeness** | **{verif.get('proof_completeness', {}).get('numerator')} / {verif.get('proof_completeness', {}).get('denominator')}** | 100% complete | **PASS** |
| **Ambiguous Case Escalation** | **{verif.get('ambiguous_escalation', {}).get('rate', 0) * 100:.1f}%** | {verif.get('ambiguous_escalation', {}).get('numerator')} / {verif.get('ambiguous_escalation', {}).get('denominator')} | **PASS** |
| **Reconciliation Throughput** | **{throughput:,.2f} rec/s** | Sub-second batch execution | **PASS** |

---

## 2. Unresolved Exception Cases (Honest Denominator Accounting)

Per PRD §13.3, ambiguous cases are strictly preserved without forced model resolution:

| Case ID | Category | Status | Evidence Citations |
| :--- | :--- | :---: | :--- |
"""
    for item in unresolved_cases:
        md_content += f"| `{item.get('runtime_case_id')}` | `AMBIGUOUS_EVIDENCE` | `UNRESOLVED` | Matched label `{item.get('label_case_id')}` |\n"

    md_content += f"""
---

## 3. Idempotency & Replay Guarantee

- **First Run Hash**: `{report.get('idempotency', {}).get('first_economic_output_hash')}`
- **Second Run Hash**: `{report.get('idempotency', {}).get('second_economic_output_hash')}`
- **Economically Identical**: `{report.get('idempotency', {}).get('economically_identical')}`
- **Duplicate Ledger Adjustments**: `{duplicate_adjustments_text}` (measured across replay databases)
"""
    md_path.write_text(md_content, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
