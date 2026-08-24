"""Independent holdout variation transform (PRD 13.3 anti-overfitting).

This module is deliberately authored separately from the main dataset
generator: it knows nothing about corpus internals, injectors, or labels. It
reshapes fully generated holdout rows so the frozen holdout is not
byte-shape-identical to the development dataset:

- row ordering is shuffled per file with a dedicated deterministic RNG;
- harmless column names are renamed via the documented ingest alias map
  (``app.importers.ingest.HEADER_ALIASES``);
- optional fields (``order_id``) are emptied on a deterministic subset of
  payment rows.

Economic preservation invariants (asserted by unit tests):

- identifiers, amounts, currencies, statuses, timestamps, and case-relevant
  references are never altered;
- the multiset of rows per file is preserved exactly (reordering only);
- the transform is deterministic for a given seed.

The holdout generation path carries empty ``row_expectations``, so row
position carries no label semantics and reordering is label-safe.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

# Written header variants per file; ingest resolves them back to canonical
# names. Only harmless aliases are permitted here.
VARIANT_COLUMNS: dict[str, tuple[str, ...]] = {
    "payments": (
        "payment_id",
        "order_id",
        "status",
        "currency",
        "gross_amount",
        "fee",
        "tax",
        "captured_at_utc",
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
        "fee",
        "tax",
        "adjustment_amount",
        "net_amount",
        "utr",
    ),
}

# Canonical -> variant key mapping applied to row dicts before serialization.
_KEY_RENAMES: dict[str, str] = {"fee_amount": "fee", "tax_amount": "tax"}

# Fraction of payment rows whose optional order_id is emptied.
_ORDER_ID_DROP_RATE = 0.3


@dataclass(frozen=True)
class VariationResult:
    """Varied rows plus the exact header order to serialize for each file."""

    rows: dict[str, list[dict[str, str]]]
    columns: dict[str, tuple[str, ...]]

    def to_manifest_columns(self) -> dict[str, list[str]]:
        return {name: list(columns) for name, columns in self.columns.items()}


def apply_holdout_variation(
    seed: int,
    rows: dict[str, list[dict[str, str]]],
    canonical_columns: dict[str, tuple[str, ...]],
) -> VariationResult:
    """Return economically identical, format-varied holdout rows."""
    varied: dict[str, list[dict[str, str]]] = {}
    columns: dict[str, tuple[str, ...]] = {}
    file_order = sorted(rows)

    for file_index, name in enumerate(file_order):
        source_rows = [dict(row) for row in rows[name]]
        rng = random.Random(f"holdout-variation|{seed}|{name}|{file_index}")

        # 1. Harmless column renames (row keys and header order).
        renames = _KEY_RENAMES if name in VARIANT_COLUMNS else {}
        for row in source_rows:
            for canonical, variant in renames.items():
                if canonical in row:
                    row[variant] = row.pop(canonical)

        # 2. Optional-field variation: empty order_id on a deterministic subset.
        if name == "payments":
            for row in source_rows:
                if row.get("order_id") and rng.random() < _ORDER_ID_DROP_RATE:
                    row["order_id"] = ""

        # 3. Deterministic row-order shuffle (labels carry no row-position
        #    semantics on this path).
        rng.shuffle(source_rows)

        varied[name] = source_rows
        columns[name] = VARIANT_COLUMNS.get(name, canonical_columns[name])

    return VariationResult(rows=varied, columns=columns)


def economic_projection(
    rows: dict[str, list[dict[str, str]]], canonical_columns: dict[str, tuple[str, ...]]
) -> dict[str, list[dict[str, Any]]]:
    """Canonical-key projection used to assert economic preservation.

    Variant keys written by :func:`apply_holdout_variation` are resolved back
    to canonical names via the ingest alias map, so the projection of varied
    rows is directly comparable to the projection of the original rows.
    """
    from app.importers.ingest import HEADER_ALIASES

    canonical_to_variant = {canonical: variant for variant, canonical in HEADER_ALIASES.items()}
    projection: dict[str, list[dict[str, Any]]] = {}
    for name, file_rows in rows.items():
        columns = canonical_columns[name]
        projected: list[dict[str, Any]] = []
        for row in file_rows:
            resolved: dict[str, Any] = {}
            for column in columns:
                value = row.get(column)
                if value is None:
                    variant = canonical_to_variant.get(column)
                    value = row.get(variant, "") if variant else ""
                resolved[column] = value
            projected.append(resolved)
        projection[name] = projected
    return projection


__all__ = [
    "VariationResult",
    "apply_holdout_variation",
    "economic_projection",
]
