"""Evaluate a companion fixture offline; never run against the user's database.

Expectation matching is case-category + evidence anchor, not a preselected match
rate. This is a fixture regression check, not the frozen holdout benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.persistence.database import Database
from app.runs import execute_run


def verify(root: Path) -> dict:
    manifest = json.loads((root / "manifest.json").read_text())
    for name, info in manifest["files"].items():
        if Path(name).name != name:
            raise ValueError("Invalid input filename")
        if (
            hashlib.sha256((root / "inputs" / name).read_bytes()).hexdigest()
            != info["sha256"]
        ):
            raise ValueError(f"File hash mismatch: {name}")
    expected = json.loads((root / "labels" / "scenario.json").read_text())
    # Runtime sees ONLY inputs; no labels or expected deltas are passed to it.
    db = Database(":memory:")
    try:
        run = execute_run(inputs_dir=root / "inputs", database=db, mode="agent")
    finally:
        db.close()
    summary = run.summary
    unmatched = list(summary["cases"])
    checks = []
    for expectation in expected["cases"]:
        found = next(
            (
                case
                for case in unmatched
                if case["category"] == expectation["kind"]
                and any(
                    item["record_type"] == expectation["source"]
                    and item["record_id"] == expectation["anchor"]
                    for item in case["evidence"]
                )
            ),
            None,
        )
        if found:
            unmatched.remove(found)
        checks.append(
            {
                "category": expectation["kind"],
                "anchor": expectation["anchor"],
                "detected": found is not None,
                "expected_delta_paise": expectation["delta_paise"],
                "actual_delta_paise": found["proposed_delta_paise"] if found else None,
                "delta_matches": found is not None
                and found["proposed_delta_paise"] == expectation["delta_paise"],
            }
        )
    passed = (
        all(check["detected"] and check["delta_matches"] for check in checks)
        and not unmatched
        and summary["quarantined_row_count"] == expected["quarantined_rows"]
        and summary["duplicate_delivery_count"] == expected["duplicate_deliveries"]
        and not summary["unaccounted_record_keys"]
        and not summary["match_invariant_violations"]
    )
    return {
        "pass": passed,
        "scope": "Known-fixture detection/delta checks, NOT held-out model accuracy",
        "provider_id": summary["provider_id"],
        "raw_rows": summary["raw_row_count"],
        "eligible_records": summary["eligible_record_count"],
        "matched_records": summary["matched_record_count"],
        "runtime_match_rate": summary["runtime_match_rate"],
        "cases_by_category": summary["cases_by_category"],
        "quarantined_rows": summary["quarantined_row_count"],
        "duplicate_deliveries": summary["duplicate_delivery_count"],
        "checks": checks,
        "unexpected_cases": unmatched,
        "economic_output_hash": summary["economic_output_hash"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = verify(args.directory)
    if args.report:
        with args.report.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["pass"] else 1)
