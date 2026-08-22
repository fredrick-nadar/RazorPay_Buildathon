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
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.evaluation.benchmark import evaluate_dataset  # noqa: E402
from app.persistence.database import Database  # noqa: E402
from app.runs import execute_run  # noqa: E402


def emit(message: str) -> None:
    print(message, flush=True)


def run_runtime_phase(
    inputs_dir: Path, scratch_dir: Path, label: str
) -> dict[str, object]:
    database_path = scratch_dir / f"benchmark-{label}.sqlite3"
    database = Database(database_path)
    try:
        result = execute_run(inputs_dir, database)
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
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="ARGUS CONTROL benchmark runner")
    parser.add_argument(
        "--dataset", required=True, help="dataset root, e.g. datasets/dev"
    )
    parser.add_argument("--mode", default="rules-only", choices=["rules-only"])
    parser.add_argument(
        "--output",
        default=None,
        help="report path (default artifacts/benchmark/<dataset>-rules-only.json)",
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
    output_path = (
        REPO_ROOT / args.output
        if args.output is not None
        else REPO_ROOT
        / "artifacts"
        / "benchmark"
        / f"{dataset_root.name}-rules-only.json"
    )

    problems: list[str] = []
    tmp_root = REPO_ROOT / "tmp"
    tmp_root.mkdir(exist_ok=True)
    scratch_dir = Path(tempfile.mkdtemp(prefix="argus-benchmark-", dir=str(tmp_root)))
    try:
        # Phase A: two independent fresh-database runs; outputs finalized here.
        first = run_runtime_phase(inputs_dir, scratch_dir, "first")
        second = run_runtime_phase(inputs_dir, scratch_dir, "second")
        problems.extend(check_runtime_contract(first))
        first_hash = str(first.get("economic_output_hash", ""))
        second_hash = str(second.get("economic_output_hash", ""))
        idempotent = first_hash == second_hash and bool(first_hash)
        if not idempotent:
            problems.append(
                f"rerun economic hash differs: {first_hash} != {second_hash}"
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
        for stale in scratch_dir.glob("benchmark-*.sqlite3"):
            stale.unlink(missing_ok=True)
        scratch_dir.rmdir()

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
        "benchmark_version": "argus-benchmark-rules-only-v1",
        "dataset": args.dataset,
        "mode": args.mode,
        "idempotency": {
            "first_economic_output_hash": first_hash,
            "second_economic_output_hash": second_hash,
            "economically_identical": idempotent,
        },
        "runtime_output_path": str(runtime_path),
        "evaluation": evaluation,
        "problems": problems,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

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


if __name__ == "__main__":
    raise SystemExit(main())
