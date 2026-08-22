"""Deterministic dataset generator CLI (PRD Phase 1).

Usage (repo root, venv Python):

    python scripts/generate_dataset.py --profile dev --seed 4104
    python scripts/generate_dataset.py --profile adversarial --seed 4105
    python scripts/check_label_isolation.py

The holdout profile is spec-only until the Phase 7 freeze: without
--unfreeze-holdout it (re)writes datasets/holdout/spec.json and exits.

This script never installs dependencies and never touches wall-clock values
inside generated files (elapsed time is printed only).
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.evaluation import dataset_io  # noqa: E402
from app.evaluation.dataset_spec import (  # noqa: E402
    HOLDOUT_DATASET_SPEC,
    HOLDOUT_SPEC_DOC,
    PROFILES,
    GenerationSpec,
)
from app.evaluation.generator import generate_dataset  # noqa: E402


def build_spec(profile: str, seed: int | None) -> GenerationSpec:
    if profile == "holdout":
        base = HOLDOUT_DATASET_SPEC
    else:
        base = PROFILES[profile]
    if seed is None:
        return base
    return dataclasses.replace(base, seed=seed)


def write_holdout_spec(output_root: Path) -> int:
    holdout_dir = output_root / "holdout"
    holdout_dir.mkdir(parents=True, exist_ok=True)
    (holdout_dir / "spec.json").write_bytes(dataset_io.json_bytes(HOLDOUT_SPEC_DOC))
    print("holdout: spec-only mode; wrote datasets/holdout/spec.json")
    print("holdout: generation requires --unfreeze-holdout (Phase 7 freeze)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an ARGUS evaluation dataset")
    parser.add_argument("--profile", required=True, choices=["dev", "adversarial", "holdout"])
    parser.add_argument("--seed", type=int, default=None, help="override the profile seed")
    parser.add_argument(
        "--output-root", default=str(REPO_ROOT / "datasets"), help="datasets root directory"
    )
    parser.add_argument(
        "--force", action="store_true", help="overwrite an existing profile directory"
    )
    parser.add_argument(
        "--unfreeze-holdout",
        action="store_true",
        help="allow holdout generation (Phase 7 freeze procedure only)",
    )
    args = parser.parse_args()

    output_root = Path(args.output_root)
    if args.profile == "holdout" and not args.unfreeze_holdout:
        return write_holdout_spec(output_root)

    spec = build_spec(args.profile, args.seed)
    profile_dir = output_root / spec.profile
    if profile_dir.exists() and not args.force:
        print(
            f"refusing to overwrite existing {profile_dir}; pass --force to regenerate"
        )
        return 2

    started = time.perf_counter()
    result = generate_dataset(spec)
    hashes = dataset_io.write_dataset(profile_dir, result)
    elapsed_ms = round((time.perf_counter() - started) * 1000)

    rows_total = sum(len(rows) for rows in result.rows.values())
    metrics = result.label_metrics
    summary = result.labels["summary"]
    print(f"profile={spec.profile} seed={spec.seed} rows={rows_total}")
    for name, rows in result.rows.items():
        print(f"  {name}: {len(rows)} rows")
    print(f"eligible_row_count={metrics['eligible_row_count']}")
    print(f"quarantine_expected_count={metrics['quarantine_expected_count']}")
    print(f"duplicate_delivery_count={metrics['duplicate_delivery_count']}")
    print(f"case_count={summary['case_count']} by_category={summary['by_category']}")
    print(f"reproducibility_hash={hashes['reproducibility_hash']}")
    print(f"labels_sha256={hashes['labels_sha256']}")
    print(f"generated in {elapsed_ms} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
