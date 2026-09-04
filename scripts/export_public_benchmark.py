"""Regenerate the label-free frontend benchmark summary from final.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.evaluation.public_summary import (
    build_public_benchmark_summary,
    canonical_benchmark_digest,
)


def main() -> int:
    source = REPO_ROOT / "artifacts" / "benchmark" / "final.json"
    destination = source.with_name("public-summary.json")
    report = json.loads(source.read_text(encoding="utf-8"))
    summary = build_public_benchmark_summary(
        report,
        source_sha256=canonical_benchmark_digest(report),
    )
    destination.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"[public-benchmark] wrote {destination.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
