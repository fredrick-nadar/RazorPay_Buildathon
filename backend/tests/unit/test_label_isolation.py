"""Label firewall tests: the green path plus planted-violation negatives.

A checker that cannot fail proves nothing, so every check is also exercised
against a planted violation in a temp location (never the real tree).
"""

from __future__ import annotations

import json
from pathlib import Path

from app.evaluation import label_firewall as lf

REPO_ROOT = Path(__file__).resolve().parents[3]


class TestGreenPath:
    def test_repository_has_no_violations(self) -> None:
        assert lf.run_all_checks(REPO_ROOT) == []

    def test_physical_layout(self) -> None:
        assert lf.collect_physical_layout_violations(REPO_ROOT) == []

    def test_frontend_has_no_label_references(self) -> None:
        assert lf.collect_frontend_violations(REPO_ROOT) == []

    def test_runtime_imports_are_clean(self) -> None:
        assert lf.collect_import_violations(REPO_ROOT) == []

    def test_runtime_literals_are_clean(self) -> None:
        assert lf.collect_literal_violations(REPO_ROOT) == []


class TestPlantedViolations:
    def test_planted_evaluation_import_is_caught(self, tmp_path: Path) -> None:
        planted = tmp_path / "runtime"
        planted.mkdir()
        (planted / "bad_import.py").write_text(
            "from app.evaluation import control_totals\n", encoding="utf-8"
        )
        violations = lf.collect_import_violations(REPO_ROOT, extra_root=planted)
        assert any("bad_import.py" in v for v in violations)

    def test_planted_relative_evaluation_import_is_caught(self, tmp_path: Path) -> None:
        planted = tmp_path / "runtime"
        planted.mkdir()
        (planted / "bad_relative.py").write_text(
            "from ..evaluation import generator\n", encoding="utf-8"
        )
        violations = lf.collect_import_violations(REPO_ROOT, extra_root=planted)
        assert any("bad_relative.py" in v for v in violations)

    def test_planted_label_path_literal_is_caught(self, tmp_path: Path) -> None:
        planted = tmp_path / "runtime"
        planted.mkdir()
        (planted / "bad_path.py").write_text('LABELS = "datasets/dev/labels"\n', encoding="utf-8")
        violations = lf.collect_literal_violations(REPO_ROOT, extra_root=planted)
        assert any("bad_path.py" in v for v in violations)

    def test_planted_label_field_name_is_caught(self, tmp_path: Path) -> None:
        planted = tmp_path / "runtime"
        planted.mkdir()
        (planted / "bad_field.py").write_text('FIELD = "must_escalate"\n', encoding="utf-8")
        violations = lf.collect_literal_violations(REPO_ROOT, extra_root=planted)
        assert any("bad_field.py" in v for v in violations)

    def test_planted_label_data_in_inputs_is_caught(self, tmp_path: Path) -> None:
        inputs = tmp_path / "dev" / "inputs"
        inputs.mkdir(parents=True)
        (inputs / "payments.csv").write_text(
            "payment_id,expected_category\npay_x,DUPLICATE_LEDGER_POSTING\n",
            encoding="utf-8",
        )
        violations = lf.collect_input_purity_violations(REPO_ROOT, tmp_path)
        assert any("expected_category" in v for v in violations)

    def test_planted_json_in_inputs_is_caught(self, tmp_path: Path) -> None:
        inputs = tmp_path / "dev" / "inputs"
        inputs.mkdir(parents=True)
        (inputs / "labels.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
        (inputs / "payments.csv").write_text("payment_id\npay_x\n", encoding="utf-8")
        violations = lf.collect_input_purity_violations(REPO_ROOT, tmp_path)
        assert any("labels.json" in v for v in violations)

    def test_physical_layout_detects_incomplete_labels_dir(self, tmp_path: Path) -> None:
        profile = tmp_path / "datasets" / "dev"
        (profile / "inputs").mkdir(parents=True)
        for name in lf.INPUT_FILE_NAMES:
            (profile / "inputs" / name).write_text("x\n", encoding="utf-8")
        (profile / "labels").mkdir(parents=True)
        (profile / "labels" / "labels.json").write_text("{}", encoding="utf-8")
        (profile / "manifest.json").write_text("{}", encoding="utf-8")
        violations = lf.collect_physical_layout_violations(tmp_path)
        assert any("labels" in v for v in violations)

    def test_physical_layout_rejects_generated_holdout(self, tmp_path: Path) -> None:
        holdout = tmp_path / "datasets" / "holdout"
        (holdout / "inputs").mkdir(parents=True)
        (holdout / "spec.json").write_text("{}", encoding="utf-8")
        violations = lf.collect_physical_layout_violations(tmp_path)
        assert any("holdout" in v for v in violations)
