"""Read-only validation of committed benchmark evidence and public claims.

Two problems this module exists to fix:

1. The first Phase 8 gate accepted a hand-written toy JSON object as a
   benchmark artifact, because it only checked ratio arithmetic. It never
   asserted the benchmark identity, the frozen holdout dataset, the evaluation
   mode, the completeness of the evaluation sections or the Phase 7 acceptance
   conditions.
2. Published figures in ``README.md`` and ``BUILD_STATUS.md`` were maintained
   by hand and had drifted from the machine artifact (``BUILD_STATUS.md``
   claimed 9,859.51 rec/s while ``artifacts/benchmark/final.json`` records
   9,656.1).

Nothing here regenerates or writes a benchmark. It reads what the benchmark
runner already produced. :func:`phase7_core_conditions` is the single shared
implementation of the Phase 7 acceptance conditions, so Phase 7 and Phase 8
cannot drift into two evaluators of different strength; Phase 8 layers the
additional release conditions on top of the same function.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

__all__ = [
    "BENCHMARK_CONTRACTS",
    "FINAL_AGENT_ARTIFACT",
    "FINAL_RULES_ONLY_ARTIFACT",
    "FINAL_SUMMARY_ARTIFACT",
    "PUBLISHED_CLAIMS",
    "PublishedClaim",
    "phase7_core_conditions",
    "renderings",
    "unresolved_inventory",
    "validate_benchmark_artifact",
    "validate_published_metrics",
    "validate_summary_derivation",
]

FINAL_AGENT_ARTIFACT = "artifacts/benchmark/final.json"
FINAL_RULES_ONLY_ARTIFACT = "artifacts/benchmark/final-rules-only.json"
FINAL_SUMMARY_ARTIFACT = "artifacts/benchmark/final_summary.md"

FROZEN_HOLDOUT_DATASET = "datasets/holdout"
MINIMUM_ELIGIBLE_RECORDS = 500

# What each committed release artifact must be. Phase 7 produces `final.json`
# in agent mode against the deterministic fake investigator, and
# `final-rules-only.json` with no investigator at all.
BENCHMARK_CONTRACTS: dict[str, dict[str, Any]] = {
    FINAL_AGENT_ARTIFACT: {
        "benchmark_version": "argus-benchmark-agent-v1",
        "mode": "agent",
        "providers": ("fake",),
        "requires_replay_diagnostics": True,
        "role": "final agent (deterministic fake investigator) evidence",
    },
    FINAL_RULES_ONLY_ARTIFACT: {
        "benchmark_version": "argus-benchmark-rules-only-v1",
        "mode": "rules-only",
        "providers": ("none",),
        "requires_replay_diagnostics": False,
        "role": "rules-only evidence",
    },
}

REQUIRED_EVALUATION_SECTIONS = (
    "case_comparison",
    "counts",
    "economic_output_hash",
    "false_relationships",
    "graph",
    "labels_loaded_after_runtime_output",
    "metrics",
    "residual_variance",
    "throughput",
    "totals_comparison",
    "unaccounted_record_keys",
    "verification",
)


def _ratio(block: object) -> tuple[int, int, float | None] | None:
    if not isinstance(block, dict):
        return None
    numerator = block.get("numerator")
    denominator = block.get("denominator")
    rate = block.get("rate")
    if not isinstance(numerator, int) or not isinstance(denominator, int):
        return None
    if rate is not None and not isinstance(rate, (int, float)):
        return None
    return numerator, denominator, None if rate is None else float(rate)


def _check_ratio(label: str, block: object, problems: list[str]) -> None:
    parsed = _ratio(block)
    if parsed is None:
        problems.append(f"{label}: missing or malformed numerator/denominator")
        return
    numerator, denominator, rate = parsed
    if numerator < 0 or denominator < 0:
        problems.append(f"{label}: negative counts")
        return
    if numerator > denominator:
        problems.append(f"{label}: numerator {numerator} exceeds denominator {denominator}")
        return
    if rate is None:
        return
    expected = 0.0 if denominator == 0 else round(numerator / denominator, 6)
    if abs(rate - expected) > 1e-6:
        problems.append(f"{label}: rate {rate} does not equal {numerator}/{denominator}")


def _require_perfect(label: str, block: object, problems: list[str]) -> None:
    _check_ratio(label, block, problems)
    parsed = _ratio(block)
    if parsed is None:
        return
    numerator, denominator, _rate = parsed
    if denominator == 0 or numerator != denominator:
        problems.append(f"{label}: {numerator}/{denominator} is not complete")


def phase7_core_conditions(label: str, data: object) -> list[str]:
    """The Phase 7 acceptance conditions, as one shared read-only check.

    Phase 7 and Phase 8 both call this, so the release gate can never be
    weaker than the phase gate whose evidence it certifies.
    """
    problems: list[str] = []
    if not isinstance(data, dict):
        return [f"{label}: artifact root is not an object"]
    evaluation = data.get("evaluation")
    if not isinstance(evaluation, dict):
        return [f"{label}: no evaluation block"]

    counts = evaluation.get("counts")
    if not isinstance(counts, dict):
        problems.append(f"{label}: no counts block")
    else:
        eligible = counts.get("eligible_canonical_records")
        computed = counts.get("computed_eligible_records")
        if not isinstance(eligible, int):
            problems.append(f"{label}: eligible_canonical_records is not an integer")
        elif eligible < MINIMUM_ELIGIBLE_RECORDS:
            problems.append(
                f"{label}: {eligible} eligible records is below the "
                f"{MINIMUM_ELIGIBLE_RECORDS}-record benchmark target"
            )
        if isinstance(eligible, int) and isinstance(computed, int) and eligible != computed:
            problems.append(
                f"{label}: eligible_canonical_records {eligible} != "
                f"computed_eligible_records {computed}"
            )
        if counts.get("row_accounting_identity_holds") is not True:
            problems.append(f"{label}: row accounting identity does not hold")

    metrics = evaluation.get("metrics")
    if not isinstance(metrics, dict):
        problems.append(f"{label}: no metrics block")
    else:
        for name, block in metrics.items():
            _check_ratio(f"{label}:{name}", block, problems)
        for name in ("match_precision", "case_classification_accuracy"):
            parsed = _ratio(metrics.get(name))
            if parsed is None:
                problems.append(f"{label}: {name} is missing")
            elif parsed[2] != 1.0:
                problems.append(f"{label}: {name} rate {parsed[2]} != 1.0")

    verification = evaluation.get("verification")
    if not isinstance(verification, dict):
        problems.append(f"{label}: no verification block")
        return problems

    false_passes = verification.get("false_verifier_pass_count")
    listed = verification.get("false_verifier_passes")
    if false_passes != 0:
        problems.append(f"{label}: false verifier passes {false_passes} != 0")
    if not isinstance(listed, list):
        problems.append(f"{label}: false_verifier_passes is not a list")
    elif isinstance(false_passes, int) and false_passes != len(listed):
        problems.append(
            f"{label}: false_verifier_pass_count {false_passes} does not match "
            f"{len(listed)} listed entries"
        )

    if verification.get("money_weighted_dry_run_error_paise") != 0:
        problems.append(
            f"{label}: money-weighted dry-run error "
            f"{verification.get('money_weighted_dry_run_error_paise')} != 0"
        )

    completeness = verification.get("proof_completeness")
    _require_perfect(f"{label}:proof_completeness", completeness, problems)
    if isinstance(completeness, dict) and completeness.get("complete") is not True:
        problems.append(f"{label}: proof completeness is not marked complete")

    return problems


def unresolved_inventory(data: dict[str, Any]) -> list[dict[str, str]]:
    """The honest unresolved-exception list recorded by the benchmark run."""
    summary = (
        data.get("evaluation", {}).get("verification", {}).get("runtime_verification_summary", {})
    )
    results = summary.get("results") if isinstance(summary, dict) else None
    if not isinstance(results, list):
        return []
    inventory: list[dict[str, str]] = []
    for entry in results:
        if isinstance(entry, dict) and entry.get("case_status") == "UNRESOLVED":
            inventory.append(
                {
                    "case_id": str(entry.get("case_id", "")),
                    "category": str(entry.get("category", "")),
                }
            )
    return inventory


def validate_benchmark_artifact(relative: str, data: object) -> list[str]:
    """Full Phase 8 release validation of one committed benchmark artifact."""
    contract = BENCHMARK_CONTRACTS.get(relative)
    if contract is None:
        return [f"{relative}: no release contract is defined for this artifact"]
    if not isinstance(data, dict):
        return [f"{relative}: artifact root is not an object"]

    problems: list[str] = []

    if data.get("benchmark_version") != contract["benchmark_version"]:
        problems.append(
            f"{relative}: benchmark_version must be {contract['benchmark_version']!r}, "
            f"got {data.get('benchmark_version')!r}"
        )
    if data.get("dataset") != FROZEN_HOLDOUT_DATASET:
        problems.append(
            f"{relative}: dataset must be the frozen holdout {FROZEN_HOLDOUT_DATASET!r}, "
            f"got {data.get('dataset')!r}"
        )
    if data.get("mode") != contract["mode"]:
        problems.append(
            f"{relative}: mode must be {contract['mode']!r} "
            f"({contract['role']}), got {data.get('mode')!r}"
        )
    if data.get("provider") not in contract["providers"]:
        problems.append(
            f"{relative}: provider must be one of {list(contract['providers'])}, "
            f"got {data.get('provider')!r}"
        )
    recorded_problems = data.get("problems")
    if not isinstance(recorded_problems, list):
        problems.append(f"{relative}: no problems list recorded")
    elif recorded_problems:
        problems.append(f"{relative}: benchmark recorded {len(recorded_problems)} problem(s)")

    evaluation = data.get("evaluation")
    if not isinstance(evaluation, dict):
        return [*problems, f"{relative}: no evaluation block"]
    missing = [name for name in REQUIRED_EVALUATION_SECTIONS if name not in evaluation]
    if missing:
        problems.append(f"{relative}: evaluation is incomplete, missing {missing}")

    problems.extend(phase7_core_conditions(relative, data))

    if evaluation.get("profile") not in ("holdout", None):
        problems.append(f"{relative}: evaluation profile {evaluation.get('profile')!r} is not holdout")
    if evaluation.get("labels_loaded_after_runtime_output") is not True:
        problems.append(f"{relative}: labels were not loaded after the runtime output")

    verification = evaluation.get("verification")
    if isinstance(verification, dict):
        _require_perfect(f"{relative}:ambiguous_escalation", verification.get("ambiguous_escalation"), problems)
        _require_perfect(f"{relative}:outcome_agreement", verification.get("outcome_agreement"), problems)
        _require_perfect(f"{relative}:delta_agreement", verification.get("delta_agreement"), problems)

    for name, expected in (
        ("totals_comparison", "equal"),
        ("residual_variance", "equal"),
    ):
        block = evaluation.get(name)
        if not isinstance(block, dict) or block.get(expected) is not True:
            problems.append(f"{relative}: {name}.{expected} is not True")
    graph = evaluation.get("graph")
    if not isinstance(graph, dict) or graph.get("referentially_valid") is not True:
        problems.append(f"{relative}: evidence graph is not referentially valid")
    for name in ("unaccounted_record_keys", "false_relationships"):
        value = evaluation.get(name)
        if not isinstance(value, list):
            problems.append(f"{relative}: {name} is missing")
        elif value:
            problems.append(f"{relative}: {name} contains {len(value)} entr(y/ies)")
    comparison = evaluation.get("case_comparison")
    if not isinstance(comparison, dict):
        problems.append(f"{relative}: no case_comparison block")
    else:
        for name in ("false_positive_cases", "missed_labels"):
            entries = comparison.get(name)
            if not isinstance(entries, list):
                problems.append(f"{relative}: case_comparison.{name} is missing")
            elif entries:
                problems.append(f"{relative}: case_comparison.{name} contains {len(entries)} entr(y/ies)")

    throughput = evaluation.get("throughput")
    if not isinstance(throughput, dict):
        problems.append(f"{relative}: no throughput block")
    else:
        rate = throughput.get("records_per_second")
        if not isinstance(rate, (int, float)) or rate <= 0:
            problems.append(f"{relative}: throughput records_per_second is missing or not positive")

    idempotency = data.get("idempotency")
    if not isinstance(idempotency, dict):
        problems.append(f"{relative}: no idempotency block")
    else:
        first = idempotency.get("first_economic_output_hash")
        second = idempotency.get("second_economic_output_hash")
        if idempotency.get("economically_identical") is not True or first != second:
            problems.append(f"{relative}: replay is not economically identical")
        if not isinstance(first, str) or len(first) != 64:
            problems.append(f"{relative}: economic output hash is missing or malformed")

    replay = data.get("replay_diagnostics")
    if contract["requires_replay_diagnostics"]:
        if not isinstance(replay, dict):
            problems.append(f"{relative}: no replay_diagnostics block")
        else:
            if replay.get("is_idempotent") is not True:
                problems.append(f"{relative}: replay diagnostics report a non-idempotent replay")
            if replay.get("duplicate_corrections_detected") != 0:
                problems.append(
                    f"{relative}: replay detected "
                    f"{replay.get('duplicate_corrections_detected')} duplicate correction(s)"
                )
            discrepancies = replay.get("discrepancies")
            if not isinstance(discrepancies, list):
                problems.append(f"{relative}: replay discrepancies list is missing")
            elif discrepancies:
                problems.append(f"{relative}: replay reported {len(discrepancies)} discrepanc(y/ies)")

    if not unresolved_inventory(data):
        problems.append(
            f"{relative}: no unresolved exception inventory; the honest unresolved "
            "list must be present in the runtime verification summary"
        )

    return problems


def validate_summary_derivation(summary_text: str, final_data: dict[str, Any]) -> list[str]:
    """`final_summary.md` must be demonstrably derived from `final.json`."""
    problems: list[str] = []
    if len(summary_text.strip()) < 400:
        problems.append(
            f"{FINAL_SUMMARY_ARTIFACT} is {len(summary_text.strip())} characters; "
            "a derived summary cannot be a placeholder"
        )
        return problems

    evaluation = final_data.get("evaluation", {})
    for label, value in (
        ("benchmark_version", final_data.get("benchmark_version")),
        ("dataset", final_data.get("dataset")),
        ("economic_output_hash", evaluation.get("economic_output_hash")),
    ):
        if not isinstance(value, str) or value not in summary_text:
            problems.append(f"{FINAL_SUMMARY_ARTIFACT} does not carry the {label} from final.json")

    metrics = evaluation.get("metrics", {})
    for name in ("match_precision", "record_match_rate", "case_classification_accuracy"):
        parsed = _ratio(metrics.get(name))
        if parsed is None:
            continue
        numerator, denominator, _rate = parsed
        if f"{numerator} / {denominator}" not in summary_text:
            problems.append(
                f"{FINAL_SUMMARY_ARTIFACT} does not report {name} as {numerator} / {denominator}"
            )

    throughput = evaluation.get("throughput", {}).get("records_per_second")
    if isinstance(throughput, (int, float)) and not any(
        rendering in summary_text for rendering in renderings(throughput)
    ):
        problems.append(
            f"{FINAL_SUMMARY_ARTIFACT} does not report the measured throughput {throughput}"
        )

    inventory = unresolved_inventory(final_data)
    for case in inventory:
        if case["case_id"] and case["case_id"] not in summary_text:
            problems.append(
                f"{FINAL_SUMMARY_ARTIFACT} omits unresolved case {case['case_id']} from final.json"
            )
    return problems


# ---------------------------------------------------------------------------
# Public claims in README.md and BUILD_STATUS.md.
# ---------------------------------------------------------------------------


def renderings(value: object) -> tuple[str, ...]:
    """Acceptable literal spellings of a measured number in prose."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return (str(value),)
    forms = {str(value), f"{value:,}"}
    if isinstance(value, float):
        forms.update({f"{value:.2f}", f"{value:,.2f}", f"{value:.6f}"})
        if value <= 1.0:
            forms.add(f"{value * 100:.1f}%")
            forms.add(f"{value * 100:.2f}%")
    return tuple(sorted(forms))


class PublishedClaim:
    """One public figure that must agree with the canonical artifact."""

    __slots__ = ("_pattern", "documents", "label", "path", "renderer")

    def __init__(
        self,
        label: str,
        pattern: str,
        path: tuple[str, ...],
        documents: tuple[str, ...],
        renderer: Any = None,
    ) -> None:
        self.label = label
        self.documents = documents
        self.path = path
        self.renderer = renderer
        self._pattern = re.compile(pattern)

    def matches(self, line: str) -> bool:
        return bool(self._pattern.search(line))

    def expected(self, final_data: dict[str, Any]) -> tuple[str, ...] | None:
        node: Any = final_data
        for key in self.path:
            if not isinstance(node, dict) or key not in node:
                return None
            node = node[key]
        if self.renderer is not None:
            return tuple(self.renderer(node))
        return renderings(node)


_DOCS = ("README.md", "BUILD_STATUS.md")


def _labelled(label: str) -> str:
    """A line that PUBLISHES a value: the label opens a list item or table cell.

    Prose that merely names a metric ("...covering precision, match rate,
    throughput...") is not a published figure and is deliberately not matched;
    only a line where the label is immediately followed by a value separator
    is treated as a claim that must agree with the artifact.
    """
    return rf"(?im)^\s*[-*|]?\s*\**{label}\**\s*[:|]"


def _fraction(block: dict[str, Any]) -> tuple[str, ...]:
    numerator = block["numerator"]
    denominator = block["denominator"]
    return (
        f"{numerator} / {denominator}",
        f"{numerator}/{denominator}",
        f"{numerator:,} / {denominator:,}",
        f"{numerator:,}/{denominator:,}",
    )


PUBLISHED_CLAIMS: tuple[PublishedClaim, ...] = (
    # Any line that publishes a records-per-second figure at all.
    PublishedClaim(
        "throughput",
        r"(?i)rec/s",
        ("evaluation", "throughput", "records_per_second"),
        _DOCS,
    ),
    PublishedClaim(
        "match precision",
        _labelled("match precision"),
        ("evaluation", "metrics", "match_precision"),
        _DOCS,
        renderer=_fraction,
    ),
    PublishedClaim(
        "record match rate",
        _labelled("record match rate"),
        ("evaluation", "metrics", "record_match_rate"),
        _DOCS,
        renderer=_fraction,
    ),
    PublishedClaim(
        "case classification accuracy",
        _labelled("case classification accuracy"),
        ("evaluation", "metrics", "case_classification_accuracy"),
        _DOCS,
        renderer=_fraction,
    ),
    PublishedClaim(
        "proof completeness",
        _labelled("proof (?:package )?completeness"),
        ("evaluation", "verification", "proof_completeness"),
        _DOCS,
        renderer=_fraction,
    ),
    PublishedClaim(
        "ambiguous escalation",
        _labelled("ambiguous (?:case )?escalation"),
        ("evaluation", "verification", "ambiguous_escalation"),
        _DOCS,
        renderer=_fraction,
    ),
    PublishedClaim(
        "eligible record count",
        _labelled("(?:holdout dataset scale|eligible canonical records)"),
        ("evaluation", "counts", "eligible_canonical_records"),
        _DOCS,
    ),
    PublishedClaim(
        "replay idempotency hash",
        _labelled("replay idempotency hash"),
        ("evaluation", "economic_output_hash"),
        _DOCS,
    ),
)


def validate_published_metrics(repo_root: Path, final_data: dict[str, Any]) -> list[str]:
    """Public figures must not contradict the canonical benchmark artifact."""
    problems: list[str] = []
    for claim in PUBLISHED_CLAIMS:
        accepted = claim.expected(final_data)
        if accepted is None:
            problems.append(f"final.json has no value for the published claim {claim.label!r}")
            continue
        for document in claim.documents:
            path = repo_root / document
            if not path.is_file():
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if not claim.matches(line):
                    continue
                if not any(rendering in line for rendering in accepted):
                    problems.append(
                        f"{document}:{number} states a {claim.label} that final.json "
                        f"does not support (expected one of {list(accepted)})"
                    )
    return problems


def load_json(path: Path) -> tuple[Any, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)
