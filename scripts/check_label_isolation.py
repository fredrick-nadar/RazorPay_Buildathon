"""Label isolation checker CLI (PRD Phase 1).

Usage (repo root):

    python scripts/check_label_isolation.py

Exit code 0 means the label firewall holds: no runtime module can reach
ground-truth labels by import, by path literal, through input files, by
physical layout, or from the frontend. Any violation prints with its
location. This script never installs dependencies.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.evaluation.label_firewall import run_all_checks  # noqa: E402


def main() -> int:
    violations = run_all_checks(REPO_ROOT)
    if violations:
        print(f"LABEL FIREWALL VIOLATIONS ({len(violations)}):")
        for violation in violations:
            print(f"  - {violation}")
        return 1
    print("label firewall holds: runtime code cannot reach ground-truth labels")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
