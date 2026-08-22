"""End-to-end rules-only run tests (PRD Phase 2 evaluation commands).

Covers the full CLI-equivalent path over the committed dev and adversarial
inputs directories - the runtime receives ONLY the inputs directory, never
the dataset parent - plus the labels-access audit-hook guard, which must run
in an isolated subprocess because audit hooks cannot be removed from the
process that installed them. No audit hook is ever installed in the main
pytest process.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.domain.enums import BatchStatus, QuarantineReason
from app.persistence.database import Database
from app.runs import execute_run

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = REPO_ROOT / "backend"

_AUDIT_RUNNER = """
import sys

def _guard(event, args):
    if event == "open":
        path = str(args[0]).replace(chr(92), "/")
        if "labels" in path.split("/"):
            print("LABELS-ACCESS:" + path)
            raise SystemExit(2)

sys.addaudithook(_guard)
sys.path.insert(0, {backend!r})
from pathlib import Path

from app.persistence.database import Database
from app.runs import execute_run

database = Database(Path({database_path!r}))
try:
    result = execute_run(Path({inputs_dir!r}), database)
finally:
    database.close()
print("RUN-COMPLETED:" + result.run_id)
"""

_CANARY_RUNNER = """
import sys

def _guard(event, args):
    if event == "open":
        path = str(args[0]).replace(chr(92), "/")
        if "labels" in path.split("/"):
            print("LABELS-ACCESS:" + path)
            raise SystemExit(2)

sys.addaudithook(_guard)
# Deliberately open the planted label file to prove the guard trips.
with open({canary_path!r}, "r", encoding="utf-8") as handle:
    handle.read()
print("CANARY-OPENED (guard failed to trip)")
"""


class TestRulesOnlyRunDev:
    def test_end_to_end_run_completes_with_output_contract(self, tmp_path: Path) -> None:
        database = Database(tmp_path / "dev-run.sqlite3")
        try:
            result = execute_run(REPO_ROOT / "datasets" / "dev" / "inputs", database)
        finally:
            database.close()
        assert result.status == BatchStatus.COMPLETED
        assert not result.reused
        summary = result.summary
        assert summary["eligible_record_count"] == 282
        assert summary["raw_row_count"] == 282
        assert summary["row_accounting"]["identity_holds"] is True
        assert summary["cases_count"] == 12
        assert summary["quarantined_row_count"] == 0
        assert summary["match_invariant_violations"] == []
        assert summary["unaccounted_record_keys"] == []
        assert summary["rule_version_manifest"]
        assert summary["financial_control_totals"]["payment_net_paise"] == 211_903_406
        assert len(result.economic_output_hash) == 64

    def test_rules_only_path_needs_no_model_configuration(self, tmp_path: Path) -> None:
        from app.config import Settings

        settings = Settings(db_path=tmp_path / "probe.sqlite3")
        assert settings.rules_only is True  # default startup is rules-only
        database = Database(settings.db_path)
        try:
            result = execute_run(REPO_ROOT / "datasets" / "dev" / "inputs", database)
        finally:
            database.close()
        assert result.status == BatchStatus.COMPLETED


class TestRulesOnlyRunAdversarial:
    def test_quarantine_and_duplicate_expectations(self, tmp_path: Path) -> None:
        database = Database(tmp_path / "adv-run.sqlite3")
        try:
            result = execute_run(REPO_ROOT / "datasets" / "adversarial" / "inputs", database)
        finally:
            database.close()
        summary = result.summary
        assert summary["eligible_record_count"] == 64
        assert summary["quarantined_row_count"] == 2
        assert summary["duplicate_delivery_count"] == 1
        reasons = {row["reason"] for row in summary["quarantined_rows"]}
        assert reasons == {
            QuarantineReason.UNSUPPORTED_CURRENCY.value,
            QuarantineReason.INVALID_TIMESTAMP.value,
        }
        assert summary["cases_count"] == 3
        assert summary["unaccounted_record_keys"] == []

    def test_duplicate_delivery_dedups_to_one_payment(self, tmp_path: Path) -> None:
        database = Database(tmp_path / "adv-dedup.sqlite3")
        try:
            execute_run(REPO_ROOT / "datasets" / "adversarial" / "inputs", database)
            rows = [dict(row) for row in database.query_all("SELECT * FROM source_rows")]
        finally:
            database.close()
        pay_rows = [row for row in rows if row["source_record_id"] == "pay_NZ3xBYxQFL"]
        states = {row["state"] for row in pay_rows}
        assert states == {"ACCEPTED", "DUPLICATE_DELIVERY"}
        accepted = [row for row in pay_rows if row["state"] == "ACCEPTED"]
        assert len(accepted) == 1


class TestLabelsAccessGuard:
    def _sandbox(self, tmp_path: Path) -> tuple[Path, Path]:
        """inputs copy plus a planted labels directory beside it."""
        sandbox = tmp_path / "sandbox"
        inputs = sandbox / "inputs"
        inputs.mkdir(parents=True)
        for csv_file in sorted((REPO_ROOT / "datasets" / "adversarial" / "inputs").glob("*.csv")):
            (inputs / csv_file.name).write_bytes(csv_file.read_bytes())
        planted = sandbox / "labels"
        planted.mkdir()
        canary = planted / "labels.json"
        canary.write_text(json.dumps({"canary": "ground-truth-bytes"}), encoding="utf-8")
        return inputs, canary

    def test_runtime_never_opens_labels_in_isolated_subprocess(self, tmp_path: Path) -> None:
        inputs, _canary = self._sandbox(tmp_path)
        code = _AUDIT_RUNNER.format(
            backend=str(BACKEND_DIR),
            database_path=str(tmp_path / "audit.sqlite3"),
            inputs_dir=str(inputs),
        )
        completed = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            cwd=str(REPO_ROOT),
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "RUN-COMPLETED:" in completed.stdout
        assert "LABELS-ACCESS:" not in completed.stdout

    def test_guard_actually_trips_on_labels_access(self, tmp_path: Path) -> None:
        _inputs, canary = self._sandbox(tmp_path)
        code = _CANARY_RUNNER.format(canary_path=str(canary))
        completed = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            cwd=str(REPO_ROOT),
        )
        assert completed.returncode == 2
        assert "LABELS-ACCESS:" in completed.stdout
