"""Dataset specifications and shared constants for ARGUS evaluation datasets.

Every dataset profile is a frozen value. The same spec (profile + seed +
generator code + dataset version) must produce byte-identical inputs, labels,
labels-manifest, and root manifest. No wall-clock, environment, or locale
input participates in generation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

DATASET_VERSION = "argus-datasets-v1"
LABEL_SCHEMA_VERSION = "argus-labels-v1"

DEV_SEED = 4104
ADVERSARIAL_SEED = 4105
HOLDOUT_SEED = 9107

WINDOW_SECONDS = 86_400

INPUT_FILES = ("payments", "refunds", "settlements", "bank_entries", "ledger_entries")

COLUMNS: dict[str, tuple[str, ...]] = {
    "payments": (
        "payment_id",
        "order_id",
        "status",
        "currency",
        "gross_amount",
        "fee_amount",
        "tax_amount",
        "captured_at_utc",
        "settlement_id",
    ),
    "refunds": (
        "refund_id",
        "payment_id",
        "status",
        "currency",
        "refund_amount",
        "created_at_utc",
        "settlement_id",
    ),
    "settlements": (
        "settlement_id",
        "settled_at_utc",
        "window_start_utc",
        "window_end_utc",
        "status",
        "currency",
        "gross_credit",
        "fee_amount",
        "tax_amount",
        "adjustment_amount",
        "net_amount",
        "utr",
    ),
    "bank_entries": (
        "bank_entry_id",
        "posted_at_utc",
        "value_date",
        "currency",
        "signed_amount",
        "narration",
        "utr",
        "account_fingerprint",
    ),
    "ledger_entries": (
        "ledger_entry_id",
        "account_code",
        "accounting_date",
        "currency",
        "signed_amount",
        "source_reference",
        "source_type",
        "description",
        "entry_origin",
    ),
}

ID_COLUMNS: dict[str, str] = {
    "payments": "payment_id",
    "refunds": "refund_id",
    "settlements": "settlement_id",
    "bank_entries": "bank_entry_id",
    "ledger_entries": "ledger_entry_id",
}


def epoch_seconds(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp())


def format_ts(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_date(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%d")


def parse_ts(text: str) -> int:
    parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return int(parsed.timestamp())


def shift_date(date_text: str, days: int) -> str:
    parsed = datetime.strptime(date_text, "%Y-%m-%d") + timedelta(days=days)
    return parsed.strftime("%Y-%m-%d")


@dataclass(frozen=True)
class GenerationSpec:
    """Frozen generation parameters; profile name plus seed define identity."""

    profile: str
    seed: int
    base_epoch_s: int
    window_count: int
    ambiguous_pair_windows: tuple[int, ...]
    payments_per_base_settlement: int
    refund_count: int = 0
    duplicate_cases: int = 0
    missing_refund_cases: int = 0
    timing_shift_cases: int = 0
    adversarial: bool = False

    def window_bounds(self, index: int) -> tuple[int, int]:
        start = self.base_epoch_s + index * WINDOW_SECONDS
        return start, start + WINDOW_SECONDS


DEV_SPEC = GenerationSpec(
    profile="dev",
    seed=DEV_SEED,
    base_epoch_s=epoch_seconds(2026, 3, 2),
    window_count=18,
    ambiguous_pair_windows=(2, 9, 15),
    payments_per_base_settlement=7,
    refund_count=18,
    duplicate_cases=3,
    missing_refund_cases=3,
    timing_shift_cases=3,
)

ADVERSARIAL_SPEC = GenerationSpec(
    profile="adversarial",
    seed=ADVERSARIAL_SEED,
    base_epoch_s=epoch_seconds(2026, 3, 30),
    window_count=6,
    ambiguous_pair_windows=(1,),
    payments_per_base_settlement=4,
    refund_count=2,
    adversarial=True,
)

# Reached only through --unfreeze-holdout at the Phase 7 freeze (PRD 16).
HOLDOUT_DATASET_SPEC = GenerationSpec(
    profile="holdout",
    seed=HOLDOUT_SEED,
    base_epoch_s=epoch_seconds(2026, 6, 1),
    window_count=20,
    ambiguous_pair_windows=(4, 11),
    payments_per_base_settlement=3,
    refund_count=6,
    duplicate_cases=2,
    missing_refund_cases=2,
    timing_shift_cases=2,
)

# Test-only scale profile for the 500+ benchmark smoke; never committed.
BENCHMARK_SPEC = GenerationSpec(
    profile="benchmark",
    seed=7001,
    base_epoch_s=epoch_seconds(2026, 5, 1),
    window_count=60,
    ambiguous_pair_windows=(3, 20, 40),
    payments_per_base_settlement=3,
    refund_count=24,
    duplicate_cases=3,
    missing_refund_cases=3,
    timing_shift_cases=3,
)

PROFILES: dict[str, GenerationSpec] = {
    "dev": DEV_SPEC,
    "adversarial": ADVERSARIAL_SPEC,
}

HOLDOUT_SPEC_DOC: dict[str, object] = {
    "profile": "holdout",
    "seed": HOLDOUT_SEED,
    "status": "SPEC_ONLY_GENERATION_FROZEN_UNTIL_PHASE_7",
    "dataset_version": DATASET_VERSION,
    "target_min_eligible_records": 100,
    "target_eligible_records": 500,
    "planned_freeze_phase": 7,
    "generation_guard": (
        "scripts/generate_dataset.py --profile holdout requires --unfreeze-holdout "
        "to generate inputs and labels"
    ),
    "seed_separation": {
        "dev": DEV_SEED,
        "adversarial": ADVERSARIAL_SEED,
        "holdout": HOLDOUT_SEED,
    },
    "variation_plan": [
        "row-order variation across all input files",
        "optional-field presence variation (for example empty UTR)",
        "date-format variation inside the documented envelope",
        "harmless column-name variation handled by explicit schema adapters",
    ],
    "note": (
        "Inputs and labels will be generated through the evaluator-only path at the "
        "Phase 7 freeze and hashed. No tuning against holdout labels before that."
    ),
}
