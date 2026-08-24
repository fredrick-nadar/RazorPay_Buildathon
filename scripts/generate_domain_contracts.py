"""Explicit generator for ``contracts/domain_enums.json``.

Run manually — never from tests:

    .venv\\Scripts\\python scripts\\generate_domain_contracts.py

The output is deterministic and committed. Python and TypeScript tests compare
against it read-only; they never regenerate or modify it. To change an enum:
update ``backend/app/domain/enums.py`` and ``frontend/src/domain/enums.ts``
together, rerun this script, and keep both contract tests green.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.config import DOMAIN_CONTRACT_VERSION  # noqa: E402
from app.domain.enums import ALL_ENUMS  # noqa: E402

OUTPUT_PATH = REPO_ROOT / "contracts" / "domain_enums.json"


def build_contract() -> dict[str, object]:
    enums: dict[str, list[str]] = {
        name: [member.value for member in enum_cls]
        for name, enum_cls in ALL_ENUMS.items()
    }
    return {
        "contract": "domain_enums",
        "version": DOMAIN_CONTRACT_VERSION,
        "description": (
            "Canonical serialization of ARGUS CONTROL domain enums. Generated ONLY by "
            "scripts/generate_domain_contracts.py; treat as read-only everywhere else."
        ),
        "enums": enums,
    }


def main() -> int:
    contract = build_contract()
    enums = contract["enums"]
    assert isinstance(enums, dict)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(contract, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUTPUT_PATH} ({len(enums)} enums)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
