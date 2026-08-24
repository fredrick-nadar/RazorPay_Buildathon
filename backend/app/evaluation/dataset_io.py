"""Deterministic dataset serialization, manifests, and hashing.

Layout written for one profile directory:

    <profile>/inputs/<source>.csv        runtime inputs (raw rows)
    <profile>/labels/labels.json         evaluator-only ground truth
    <profile>/labels/manifest.json       evaluator-only label integrity metadata
    <profile>/manifest.json              raw input facts ONLY

Review correction: the root manifest is strictly input-only - dataset
version, profile, seed, column names, raw row counts, file sha256 hashes,
and the input reproducibility hash. Eligible/quarantine/duplicate counts and
normalized financial totals (which require knowing which rows are valid or
anomalous) live only in the evaluator-side labels manifest and in phase
artifacts.

Determinism: CSV via csv.writer with ``lineterminator="\\n"`` and
QUOTE_MINIMAL; JSON via ``sort_keys=True, indent=2`` plus a trailing
newline; hashes are sha256 over exact bytes. No wall-clock input.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any

from app.evaluation.dataset_spec import (
    COLUMNS,
    DATASET_VERSION,
    INPUT_FILES,
    LABEL_SCHEMA_VERSION,
)
from app.evaluation.generator import GenerationResult


def csv_bytes(columns: tuple[str, ...], rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer, fieldnames=list(columns), lineterminator="\n", quoting=csv.QUOTE_MINIMAL
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8")


def json_bytes(obj: dict[str, Any]) -> bytes:
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return text.encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def reproducibility_hash(seed: int, dataset_version: str, file_hashes: dict[str, str]) -> str:
    parts = [f"{seed}", dataset_version]
    parts.extend(f"{relative}:{digest}" for relative, digest in sorted(file_hashes.items()))
    return sha256_bytes("|".join(parts).encode("utf-8"))


def write_dataset(root: Path, result: GenerationResult) -> dict[str, str]:
    """Write one profile directory; return hashes for caller reporting."""
    inputs_dir = root / "inputs"
    labels_dir = root / "labels"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)

    encoded: dict[str, bytes] = {}
    files_info: dict[str, dict[str, Any]] = {}
    for name in INPUT_FILES:
        columns = result.columns.get(name, COLUMNS[name]) if result.columns else COLUMNS[name]
        data = csv_bytes(columns, result.rows[name])
        encoded[f"inputs/{name}.csv"] = data
        files_info[f"inputs/{name}.csv"] = {
            "rows": len(result.rows[name]),
            "sha256": sha256_bytes(data),
            "columns": list(columns),
        }

    labels_data = json_bytes(result.labels)
    seed = result.spec.seed
    repro = reproducibility_hash(
        seed, DATASET_VERSION, {relative: info["sha256"] for relative, info in files_info.items()}
    )
    root_manifest: dict[str, Any] = {
        "dataset_version": DATASET_VERSION,
        "profile": result.spec.profile,
        "seed": seed,
        "files": files_info,
        "reproducibility_hash": repro,
    }
    labels_manifest: dict[str, Any] = {
        "label_schema_version": LABEL_SCHEMA_VERSION,
        "dataset_version": DATASET_VERSION,
        "profile": result.spec.profile,
        "seed": seed,
        "labels_sha256": sha256_bytes(labels_data),
        "case_count": len(result.labels.get("cases", [])),
        "row_expectation_count": len(result.labels.get("row_expectations", [])),
        **result.label_metrics,
    }

    for relative, data in encoded.items():
        (root / relative).write_bytes(data)
    (labels_dir / "labels.json").write_bytes(labels_data)
    (labels_dir / "manifest.json").write_bytes(json_bytes(labels_manifest))
    (root / "manifest.json").write_bytes(json_bytes(root_manifest))
    return {
        "reproducibility_hash": repro,
        "labels_sha256": sha256_bytes(labels_data),
    }
