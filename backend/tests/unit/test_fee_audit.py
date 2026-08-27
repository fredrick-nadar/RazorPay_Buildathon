"""Unit tests for Automated MDR & GST Fee Variance Reconciler."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.fee_audit import audit_run_fees
from app.main import create_app
from app.runs import execute_run


def test_fee_audit_integer_paise_math(tmp_path: Path) -> None:
    settings = Settings(ARGUS_DB_PATH=str(tmp_path / "test_fee_audit.db"))
    app = create_app(settings)

    with TestClient(app):
        repo_root = Path(__file__).resolve().parents[3]
        inputs_dir = repo_root / "datasets" / "dev" / "inputs"

        # Run a batch to populate database
        run_res = execute_run(
            inputs_dir=inputs_dir,
            database=app.state.db,
            mode="rules-only",
            force=True,
        )
        assert run_res.run_id is not None

        summary = audit_run_fees(
            app.state.db, run_res.run_id, contractual_mdr_bps=200, contractual_gst_bps=1800
        )

        assert summary.run_id == run_res.run_id
        assert summary.total_gmv_paise > 0
        assert isinstance(summary.total_expected_fee_paise, int)
        assert isinstance(summary.total_actual_fee_paise, int)
        assert isinstance(summary.total_expected_gst_paise, int)
        assert isinstance(summary.total_actual_gst_paise, int)
        assert isinstance(summary.net_leakage_paise, int)

        for item in summary.items:
            assert isinstance(item.gross_amount_paise, int)
            assert isinstance(item.expected_fee_paise, int)
            assert isinstance(item.expected_gst_paise, int)
            assert isinstance(item.variance_paise, int)


def test_fee_audit_endpoint(tmp_path: Path) -> None:
    settings = Settings(ARGUS_DB_PATH=str(tmp_path / "test_fee_api.db"))
    app = create_app(settings)

    repo_root = Path(__file__).resolve().parents[3]
    inputs_dir = repo_root / "datasets" / "dev" / "inputs"

    with TestClient(app) as client:
        run_res = execute_run(
            inputs_dir=inputs_dir,
            database=app.state.db,
            mode="rules-only",
            force=True,
        )
        assert run_res.run_id is not None

        res = client.get(f"/api/v1/runs/{run_res.run_id}/fee-audit?mdr_bps=200&gst_bps=1800")
        assert res.status_code == 200
        data = res.json()
        assert data["run_id"] == run_res.run_id
        assert "total_gmv_paise" in data
        assert "net_leakage_paise" in data
        assert "items" in data
