"""Deterministic event-failure injector (PRD Phase 6).

Injects realistic operational anomalies into event streams or dataset files:
- Duplicate deliveries (webhook replays, duplicate bank exports);
- Out-of-order deliveries (settlements/bank credits preceding payments);
- Delayed or missing events (dropped webhooks, processing timeouts);
- Corrupted payloads (invalid money formatting, schema tampering, bad dates).
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class FailureType(StrEnum):
    """Supported failure injection types."""

    DUPLICATE_DELIVERY = "DUPLICATE_DELIVERY"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    DELAYED_OR_MISSING = "DELAYED_OR_MISSING"
    CORRUPTED_PAYLOAD = "CORRUPTED_PAYLOAD"


@dataclass(frozen=True)
class InjectedFailureRecord:
    failure_type: FailureType
    source_file: str
    target_id: str | None
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FailureInjectionResult:
    seed: int
    injected_counts: dict[str, int]
    original_row_count: int
    final_row_count: int
    injections: list[InjectedFailureRecord]

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "injected_counts": self.injected_counts,
            "original_row_count": self.original_row_count,
            "final_row_count": self.final_row_count,
            "injections": [
                {
                    "failure_type": inj.failure_type.value,
                    "source_file": inj.source_file,
                    "target_id": inj.target_id,
                    "description": inj.description,
                    "metadata": inj.metadata,
                }
                for inj in self.injections
            ],
        }


class EventFailureInjector:
    """Deterministic, seed-based event failure simulator."""

    def __init__(self, seed: int = 42) -> None:
        self.seed = seed
        self._rng = random.Random(seed)

    def inject_duplicate_rows(
        self,
        rows: list[dict[str, str]],
        source_file: str,
        id_column: str,
        rate: float = 0.1,
    ) -> tuple[list[dict[str, str]], list[InjectedFailureRecord]]:
        """Duplicate a deterministic subset of rows in the input stream."""
        if not rows:
            return [], []

        result_rows = list(rows)
        injections: list[InjectedFailureRecord] = []
        dup_count = max(1, int(len(rows) * rate)) if rate > 0 else 0
        indices_to_dup = sorted(self._rng.sample(range(len(rows)), min(dup_count, len(rows))))

        for idx in indices_to_dup:
            target_row = rows[idx]
            target_id = target_row.get(id_column)
            # Append duplicate row
            insert_pos = self._rng.randint(idx + 1, len(result_rows))
            result_rows.insert(insert_pos, dict(target_row))
            injections.append(
                InjectedFailureRecord(
                    failure_type=FailureType.DUPLICATE_DELIVERY,
                    source_file=source_file,
                    target_id=target_id,
                    description=f"Duplicate row for {target_id} inserted at position {insert_pos}",
                    metadata={"original_position": idx, "duplicate_position": insert_pos},
                )
            )

        return result_rows, injections

    def inject_out_of_order_rows(
        self,
        rows: list[dict[str, str]],
        source_file: str,
        id_column: str,
        distance: int = 3,
    ) -> tuple[list[dict[str, str]], list[InjectedFailureRecord]]:
        """Permute row sequence by shifting rows forward or backward."""
        if len(rows) < 2:
            return list(rows), []

        result_rows = list(rows)
        injections: list[InjectedFailureRecord] = []
        num_swaps = max(1, len(rows) // 4)

        for _ in range(num_swaps):
            i = self._rng.randint(0, len(result_rows) - 1)
            offset = self._rng.randint(1, min(distance, len(result_rows) - 1))
            j = (i + offset) % len(result_rows)
            result_rows[i], result_rows[j] = result_rows[j], result_rows[i]
            injections.append(
                InjectedFailureRecord(
                    failure_type=FailureType.OUT_OF_ORDER,
                    source_file=source_file,
                    target_id=result_rows[i].get(id_column),
                    description=f"Swapped row positions {i} and {j}",
                    metadata={"from_position": i, "to_position": j},
                )
            )

        return result_rows, injections

    def inject_missing_rows(
        self,
        rows: list[dict[str, str]],
        source_file: str,
        id_column: str,
        rate: float = 0.05,
    ) -> tuple[list[dict[str, str]], list[InjectedFailureRecord]]:
        """Simulate dropped/missing rows from the input stream."""
        if not rows:
            return [], []

        drop_count = max(1, int(len(rows) * rate)) if rate > 0 else 0
        indices_to_drop = set(self._rng.sample(range(len(rows)), min(drop_count, len(rows))))

        result_rows: list[dict[str, str]] = []
        injections: list[InjectedFailureRecord] = []

        for idx, row in enumerate(rows):
            if idx in indices_to_drop:
                target_id = row.get(id_column)
                injections.append(
                    InjectedFailureRecord(
                        failure_type=FailureType.DELAYED_OR_MISSING,
                        source_file=source_file,
                        target_id=target_id,
                        description=f"Dropped row {target_id} from input stream",
                        metadata={"original_position": idx},
                    )
                )
            else:
                result_rows.append(row)

        return result_rows, injections

    def inject_corrupted_rows(
        self,
        rows: list[dict[str, str]],
        source_file: str,
        id_column: str,
        rate: float = 0.05,
    ) -> tuple[list[dict[str, str]], list[InjectedFailureRecord]]:
        """Corrupt field values (e.g. malformed currency or invalid money) to trigger quarantine."""
        if not rows:
            return [], []

        corrupt_count = max(1, int(len(rows) * rate)) if rate > 0 else 0
        indices_to_corrupt = set(self._rng.sample(range(len(rows)), min(corrupt_count, len(rows))))

        result_rows: list[dict[str, str]] = []
        injections: list[InjectedFailureRecord] = []

        for idx, row in enumerate(rows):
            row_copy = dict(row)
            if idx in indices_to_corrupt:
                target_id = row_copy.get(id_column)
                # Apply corruption
                corruption_choice = self._rng.choice(["currency", "money", "date"])
                if corruption_choice == "currency":
                    row_copy["currency"] = "USD"
                elif corruption_choice == "money":
                    for money_col in (
                        "gross_amount",
                        "refund_amount",
                        "net_amount",
                        "signed_amount",
                        "gross_credit",
                    ):
                        if money_col in row_copy:
                            row_copy[money_col] = "INVALID_AMT"
                            break
                elif corruption_choice == "date":
                    for date_col in ("captured_at_utc", "created_at_utc", "settled_at_utc"):
                        if date_col in row_copy:
                            row_copy[date_col] = "NOT_A_DATE"
                            break

                injections.append(
                    InjectedFailureRecord(
                        failure_type=FailureType.CORRUPTED_PAYLOAD,
                        source_file=source_file,
                        target_id=target_id,
                        description=f"Corrupted {corruption_choice} in row {target_id}",
                        metadata={"corruption_type": corruption_choice, "original_position": idx},
                    )
                )
            result_rows.append(row_copy)

        return result_rows, injections

    def inject_dataset(
        self,
        src_inputs_dir: Path,
        dest_inputs_dir: Path,
        failure_types: list[FailureType] | None = None,
    ) -> FailureInjectionResult:
        """Read a dataset inputs directory, apply failures, and write to dest directory."""
        dest_inputs_dir.mkdir(parents=True, exist_ok=True)
        if failure_types is None:
            failure_types = [
                FailureType.DUPLICATE_DELIVERY,
                FailureType.OUT_OF_ORDER,
                FailureType.DELAYED_OR_MISSING,
                FailureType.CORRUPTED_PAYLOAD,
            ]

        file_specs = [
            ("payments.csv", "payment_id"),
            ("refunds.csv", "refund_id"),
            ("settlements.csv", "settlement_id"),
            ("bank_entries.csv", "bank_entry_id"),
            ("ledger_entries.csv", "ledger_entry_id"),
        ]

        all_injections: list[InjectedFailureRecord] = []
        original_total = 0
        final_total = 0

        for filename, id_col in file_specs:
            src_file = src_inputs_dir / filename
            dest_file = dest_inputs_dir / filename
            if not src_file.is_file():
                continue

            with src_file.open("r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames or []
                rows = list(reader)

            original_total += len(rows)
            current_rows = rows

            # Apply each enabled failure type in sequence
            if FailureType.DUPLICATE_DELIVERY in failure_types:
                current_rows, injs = self.inject_duplicate_rows(
                    current_rows, filename, id_col, rate=0.08
                )
                all_injections.extend(injs)

            if FailureType.OUT_OF_ORDER in failure_types:
                current_rows, injs = self.inject_out_of_order_rows(
                    current_rows, filename, id_col, distance=2
                )
                all_injections.extend(injs)

            if FailureType.DELAYED_OR_MISSING in failure_types:
                current_rows, injs = self.inject_missing_rows(
                    current_rows, filename, id_col, rate=0.04
                )
                all_injections.extend(injs)

            if FailureType.CORRUPTED_PAYLOAD in failure_types:
                current_rows, injs = self.inject_corrupted_rows(
                    current_rows, filename, id_col, rate=0.04
                )
                all_injections.extend(injs)

            final_total += len(current_rows)

            with dest_file.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(current_rows)

        counts_by_type: dict[str, int] = {}
        for inj in all_injections:
            k = inj.failure_type.value
            counts_by_type[k] = counts_by_type.get(k, 0) + 1

        return FailureInjectionResult(
            seed=self.seed,
            injected_counts=counts_by_type,
            original_row_count=original_total,
            final_row_count=final_total,
            injections=all_injections,
        )
