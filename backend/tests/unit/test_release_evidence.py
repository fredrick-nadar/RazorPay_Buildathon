"""Benchmark evidence and published-claim agreement.

Reproduces and pins two defects: a hand-written toy JSON object passed as a
benchmark artifact, and hand-maintained public figures had drifted from the
machine artifact (BUILD_STATUS.md said 9,859.51 rec/s while final.json records
9,656.1).

Every positive case validates the REAL committed artifacts; negative cases
mutate a copy of the real structure rather than inventing a plausible-looking
stub, so a passing test cannot be an artefact of a weak fixture.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts"
FINAL_PATH = REPO_ROOT / "artifacts" / "benchmark" / "final.json"
RULES_ONLY_PATH = REPO_ROOT / "artifacts" / "benchmark" / "final-rules-only.json"
SUMMARY_PATH = REPO_ROOT / "artifacts" / "benchmark" / "final_summary.md"


@pytest.fixture(scope="module")
def evidence() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "argus_release_evidence", SCRIPTS_DIR / "release_evidence.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def final() -> dict[str, Any]:
    return json.loads(FINAL_PATH.read_text(encoding="utf-8"))


def _mutated(final: dict[str, Any], path: tuple[str, ...], value: Any) -> dict[str, Any]:
    data = copy.deepcopy(final)
    node: Any = data
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return data


# ---------------------------------------------------------------------------
# The committed artifacts must satisfy the real release contract.
# ---------------------------------------------------------------------------


def test_committed_final_agent_artifact_satisfies_the_contract(
    evidence: ModuleType, final: dict[str, Any]
) -> None:
    assert evidence.validate_benchmark_artifact(evidence.FINAL_AGENT_ARTIFACT, final) == []


def test_committed_rules_only_artifact_is_identified_as_rules_only_evidence(
    evidence: ModuleType,
) -> None:
    data = json.loads(RULES_ONLY_PATH.read_text(encoding="utf-8"))
    assert data["mode"] == "rules-only"
    assert data["provider"] == "none"
    assert evidence.validate_benchmark_artifact(evidence.FINAL_RULES_ONLY_ARTIFACT, data) == []


def test_a_tiny_hand_written_object_cannot_pass(evidence: ModuleType) -> None:
    toy = {
        "evaluation": {
            "counts": {"eligible_canonical_records": 1880, "computed_eligible_records": 1880},
            "metrics": {"match_precision": {"numerator": 1, "denominator": 1, "rate": 1.0}},
            "verification": {"false_verifier_pass_count": 0, "false_verifier_passes": []},
        }
    }
    problems = evidence.validate_benchmark_artifact(evidence.FINAL_AGENT_ARTIFACT, toy)
    assert len(problems) > 5
    assert any("benchmark_version" in problem for problem in problems)
    assert any("dataset must be the frozen holdout" in problem for problem in problems)


def test_an_unknown_artifact_has_no_contract(evidence: ModuleType) -> None:
    relative = "artifacts/benchmark/other.json"
    problems = evidence.validate_benchmark_artifact(relative, {})
    assert problems == [f"{relative}: no release contract is defined for this artifact"]


# ---------------------------------------------------------------------------
# Identity and gate conditions.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        (("benchmark_version",), "argus-benchmark-v0", "benchmark_version"),
        (("dataset",), "datasets/dev", "frozen holdout"),
        (("mode",), "rules-only", "mode must be 'agent'"),
        (("provider",), "groq", "provider must be one of"),
        (("problems",), ["a recorded problem"], "recorded 1 problem"),
    ],
)
def test_wrong_identity_fields_are_rejected(
    evidence: ModuleType, final: dict[str, Any], path: tuple[str, ...], value: Any, expected: str
) -> None:
    problems = evidence.validate_benchmark_artifact(
        evidence.FINAL_AGENT_ARTIFACT, _mutated(final, path, value)
    )
    assert any(expected in problem for problem in problems), problems


def test_incomplete_evaluation_is_rejected(evidence: ModuleType, final: dict[str, Any]) -> None:
    data = copy.deepcopy(final)
    del data["evaluation"]["graph"]
    del data["evaluation"]["totals_comparison"]
    problems = evidence.validate_benchmark_artifact(evidence.FINAL_AGENT_ARTIFACT, data)
    assert any("evaluation is incomplete" in problem for problem in problems)


def test_too_few_eligible_records_is_rejected(evidence: ModuleType, final: dict[str, Any]) -> None:
    data = _mutated(final, ("evaluation", "counts", "eligible_canonical_records"), 120)
    problems = evidence.validate_benchmark_artifact(evidence.FINAL_AGENT_ARTIFACT, data)
    assert any("below the 500-record benchmark target" in problem for problem in problems)


def test_broken_row_accounting_identity_is_rejected(
    evidence: ModuleType, final: dict[str, Any]
) -> None:
    data = _mutated(final, ("evaluation", "counts", "row_accounting_identity_holds"), False)
    problems = evidence.validate_benchmark_artifact(evidence.FINAL_AGENT_ARTIFACT, data)
    assert any("row accounting identity" in problem for problem in problems)


def test_nonzero_false_verifier_passes_are_rejected(
    evidence: ModuleType, final: dict[str, Any]
) -> None:
    data = _mutated(final, ("evaluation", "verification", "false_verifier_pass_count"), 2)
    problems = evidence.validate_benchmark_artifact(evidence.FINAL_AGENT_ARTIFACT, data)
    assert any("false verifier passes 2 != 0" in problem for problem in problems)


def test_nonzero_dry_run_error_is_rejected(evidence: ModuleType, final: dict[str, Any]) -> None:
    data = _mutated(final, ("evaluation", "verification", "money_weighted_dry_run_error_paise"), 1)
    problems = evidence.validate_benchmark_artifact(evidence.FINAL_AGENT_ARTIFACT, data)
    assert any("dry-run error" in problem for problem in problems)


def test_incomplete_proof_completeness_is_rejected(
    evidence: ModuleType, final: dict[str, Any]
) -> None:
    data = _mutated(
        final,
        ("evaluation", "verification", "proof_completeness"),
        {"numerator": 17, "denominator": 18, "complete": False},
    )
    problems = evidence.validate_benchmark_artifact(evidence.FINAL_AGENT_ARTIFACT, data)
    assert any("is not complete" in problem for problem in problems)


def test_partial_ambiguous_escalation_is_rejected(
    evidence: ModuleType, final: dict[str, Any]
) -> None:
    data = _mutated(
        final,
        ("evaluation", "verification", "ambiguous_escalation"),
        {"numerator": 3, "denominator": 5, "rate": 0.6},
    )
    problems = evidence.validate_benchmark_artifact(evidence.FINAL_AGENT_ARTIFACT, data)
    assert any("ambiguous_escalation" in problem for problem in problems)


def test_broken_replay_idempotency_is_rejected(evidence: ModuleType, final: dict[str, Any]) -> None:
    data = _mutated(final, ("idempotency", "second_economic_output_hash"), "f" * 64)
    problems = evidence.validate_benchmark_artifact(evidence.FINAL_AGENT_ARTIFACT, data)
    assert any("not economically identical" in problem for problem in problems)


def test_duplicate_corrections_in_replay_are_rejected(
    evidence: ModuleType, final: dict[str, Any]
) -> None:
    data = _mutated(final, ("replay_diagnostics", "duplicate_corrections_detected"), 4)
    problems = evidence.validate_benchmark_artifact(evidence.FINAL_AGENT_ARTIFACT, data)
    assert any("duplicate correction" in problem for problem in problems)


def test_missing_replay_diagnostics_are_rejected_for_the_agent_artifact(
    evidence: ModuleType, final: dict[str, Any]
) -> None:
    data = _mutated(final, ("replay_diagnostics",), None)
    problems = evidence.validate_benchmark_artifact(evidence.FINAL_AGENT_ARTIFACT, data)
    assert any("no replay_diagnostics" in problem for problem in problems)


def test_missing_throughput_is_rejected(evidence: ModuleType, final: dict[str, Any]) -> None:
    data = _mutated(final, ("evaluation", "throughput"), {"eligible_records": 1880})
    problems = evidence.validate_benchmark_artifact(evidence.FINAL_AGENT_ARTIFACT, data)
    assert any("records_per_second" in problem for problem in problems)


def test_missing_unresolved_inventory_is_rejected(
    evidence: ModuleType, final: dict[str, Any]
) -> None:
    data = copy.deepcopy(final)
    data["evaluation"]["verification"]["runtime_verification_summary"]["results"] = []
    problems = evidence.validate_benchmark_artifact(evidence.FINAL_AGENT_ARTIFACT, data)
    assert any("unresolved exception inventory" in problem for problem in problems)


def test_unresolved_inventory_lists_the_ambiguous_cases(
    evidence: ModuleType, final: dict[str, Any]
) -> None:
    inventory = evidence.unresolved_inventory(final)
    assert len(inventory) == 5
    assert {case["category"] for case in inventory} == {"AMBIGUOUS_EVIDENCE"}


# ---------------------------------------------------------------------------
# final_summary.md must be derived from final.json.
# ---------------------------------------------------------------------------


def test_committed_summary_is_derived_from_the_committed_artifact(
    evidence: ModuleType, final: dict[str, Any]
) -> None:
    text = SUMMARY_PATH.read_text(encoding="utf-8")
    assert evidence.validate_summary_derivation(text, final) == []


def test_a_one_word_summary_is_not_a_derived_summary(
    evidence: ModuleType, final: dict[str, Any]
) -> None:
    problems = evidence.validate_summary_derivation("summary", final)
    assert any("cannot be a placeholder" in problem for problem in problems)


def test_a_summary_omitting_an_unresolved_case_is_rejected(
    evidence: ModuleType, final: dict[str, Any]
) -> None:
    text = SUMMARY_PATH.read_text(encoding="utf-8")
    dropped = evidence.unresolved_inventory(final)[0]["case_id"]
    problems = evidence.validate_summary_derivation(text.replace(dropped, "case-removed"), final)
    assert any(dropped in problem for problem in problems)


def test_a_summary_with_the_wrong_throughput_is_rejected(
    evidence: ModuleType, final: dict[str, Any]
) -> None:
    text = SUMMARY_PATH.read_text(encoding="utf-8").replace("9,656.10", "9,859.51")
    problems = evidence.validate_summary_derivation(text, final)
    assert any("measured throughput" in problem for problem in problems)


# ---------------------------------------------------------------------------
# Public claims.
# ---------------------------------------------------------------------------


def test_repository_public_metrics_agree_with_the_canonical_artifact(
    evidence: ModuleType, final: dict[str, Any]
) -> None:
    assert evidence.validate_published_metrics(REPO_ROOT, final) == []


def test_the_9859_throughput_drift_is_reproduced_and_rejected(
    evidence: ModuleType, final: dict[str, Any], tmp_path: Path
) -> None:
    """The exact defect found in review: a published rate the artifact denies."""
    measured = final["evaluation"]["throughput"]["records_per_second"]
    assert measured == 9656.1, "canonical artifact value changed; update this regression"
    (tmp_path / "BUILD_STATUS.md").write_text(
        "- Throughput: 9,859.51 rec/s (final holdout agent-mode benchmark run).\n",
        encoding="utf-8",
    )
    problems = evidence.validate_published_metrics(tmp_path, final)
    assert any("throughput" in problem for problem in problems)
    assert any("9,656.10" in problem for problem in problems)


def test_a_correctly_published_throughput_is_accepted(
    evidence: ModuleType, final: dict[str, Any], tmp_path: Path
) -> None:
    (tmp_path / "BUILD_STATUS.md").write_text(
        "- Throughput: 9,656.10 rec/s (committed Phase 7 artifact value).\n", encoding="utf-8"
    )
    assert evidence.validate_published_metrics(tmp_path, final) == []


def test_prose_naming_a_metric_without_a_value_is_not_a_claim(
    evidence: ModuleType, final: dict[str, Any], tmp_path: Path
) -> None:
    (tmp_path / "README.md").write_text(
        "The report covers precision, match rate, proof completeness and throughput.\n",
        encoding="utf-8",
    )
    assert evidence.validate_published_metrics(tmp_path, final) == []


@pytest.mark.parametrize(
    "line",
    [
        "- Match Precision: 100.0% (900 / 1,124 explicit matches).",
        "- Record Match Rate: 99.15% (1,000 / 1,880).",
        "- Case Classification Accuracy: 100.0% (20 / 23).",
        "- Proof Package Completeness: 17 / 18 (94%).",
        "- Ambiguous Case Escalation: 4 / 5.",
        "- Replay Idempotency Hash: `deadbeef`.",
    ],
)
def test_stale_published_figures_are_rejected(
    evidence: ModuleType, final: dict[str, Any], tmp_path: Path, line: str
) -> None:
    (tmp_path / "BUILD_STATUS.md").write_text(line + "\n", encoding="utf-8")
    assert evidence.validate_published_metrics(tmp_path, final), line


# ---------------------------------------------------------------------------
# Phase 7 and Phase 8 share one evaluator.
# ---------------------------------------------------------------------------


def test_phase7_core_conditions_pass_on_the_committed_artifact(
    evidence: ModuleType, final: dict[str, Any]
) -> None:
    assert evidence.phase7_core_conditions(evidence.FINAL_AGENT_ARTIFACT, final) == []


def test_phase7_core_conditions_are_a_subset_of_the_release_conditions(
    evidence: ModuleType, final: dict[str, Any]
) -> None:
    broken = _mutated(final, ("evaluation", "verification", "false_verifier_pass_count"), 1)
    core = evidence.phase7_core_conditions(evidence.FINAL_AGENT_ARTIFACT, broken)
    release = evidence.validate_benchmark_artifact(evidence.FINAL_AGENT_ARTIFACT, broken)
    assert core, "the shared conditions must catch this"
    assert set(core).issubset(set(release)), "release must never be weaker than phase 7"
