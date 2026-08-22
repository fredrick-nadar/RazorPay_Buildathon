"""Domain enum contract consistency (read-only).

``contracts/domain_enums.json`` is generated ONLY by
``scripts/generate_domain_contracts.py`` and committed. These tests never
write or regenerate it; they verify that the Python enums match the frozen
contract exactly, in the same order, with the same members.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.domain.enums import ALL_ENUMS

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPO_ROOT / "contracts" / "domain_enums.json"


def load_contract() -> dict[str, object]:
    assert CONTRACT_PATH.is_file(), (
        f"{CONTRACT_PATH} is missing; run scripts/generate_domain_contracts.py explicitly"
    )
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def test_contract_file_declares_the_expected_schema() -> None:
    contract = load_contract()
    assert contract["contract"] == "domain_enums"
    assert isinstance(contract["version"], str) and contract["version"]
    assert isinstance(contract["enums"], dict)


def test_every_enum_matches_contract_exactly() -> None:
    contract_enums = load_contract()["enums"]
    assert isinstance(contract_enums, dict)
    assert set(contract_enums) == set(ALL_ENUMS), "enum registry and contract name sets differ"
    for name, enum_cls in ALL_ENUMS.items():
        expected = contract_enums[name]
        actual = [member.value for member in enum_cls]
        assert actual == expected, f"{name} drifted from the frozen contract"


def test_enums_serialize_to_stable_strings() -> None:
    for enum_cls in ALL_ENUMS.values():
        for member in enum_cls:
            assert member.value == member.name
            assert json.dumps(member.value) == f'"{member.name}"'


def test_frozen_taxonomy_and_outcomes_are_complete() -> None:
    # PRD 4.2: exactly these four exception classes.
    from app.domain.enums import CaseStatus, ExceptionCategory, ReasonCode

    assert [member.value for member in ExceptionCategory] == [
        "DUPLICATE_LEDGER_POSTING",
        "MISSING_REFUND_POSTING",
        "SETTLEMENT_TIMING_WINDOW_SHIFT",
        "AMBIGUOUS_EVIDENCE",
    ]
    # PRD 4.3: mandatory case outcomes.
    assert {member.value for member in CaseStatus} >= {
        "VERIFIED_RESOLVED",
        "APPROVAL_REQUIRED",
        "SIMULATED_APPLIED",
        "UNRESOLVED",
        "INVESTIGATION_FAILED",
    }
    # PRD 9.7: the eleven stable reason codes.
    assert len(list(ReasonCode)) == 11
