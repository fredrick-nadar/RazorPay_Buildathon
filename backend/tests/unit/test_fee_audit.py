"""Unit tests for the automated MDR & GST fee variance reconciler."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.fee_audit import audit_run_fees
from app.domain.fee_policy import FEE_POLICY_VERSION, resolve_fee_policy
from app.main import create_app
from app.runs import execute_run


def _dev_inputs() -> Path:
    return Path(__file__).resolve().parents[3] / "datasets" / "dev" / "inputs"


def test_fee_audit_integer_paise_math(tmp_path: Path) -> None:
    settings = Settings(db_path=tmp_path / "test_fee_audit.db", _env_file=None)
    app = create_app(settings)

    with TestClient(app):
        run_res = execute_run(
            inputs_dir=_dev_inputs(),
            database=app.state.db,
            mode="rules-only",
            force=True,
        )
        assert run_res.run_id is not None

        summary = audit_run_fees(app.state.db, run_res.run_id, policy=resolve_fee_policy(settings))

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


def test_fee_audit_reports_the_configured_synthetic_policy(tmp_path: Path) -> None:
    """The audit identifies the basis of its own figures as synthetic."""
    settings = Settings(db_path=tmp_path / "policy.db", _env_file=None)
    app = create_app(settings)

    with TestClient(app):
        run_res = execute_run(
            inputs_dir=_dev_inputs(), database=app.state.db, mode="rules-only", force=True
        )
        policy = resolve_fee_policy(settings)
        summary = audit_run_fees(app.state.db, run_res.run_id, policy=policy)

    assert summary.policy["policy_version"] == FEE_POLICY_VERSION
    assert summary.policy["data_classification"] == "SYNTHETIC_ONLY"
    assert summary.policy["source"] == "CONFIGURED_SYNTHETIC_MERCHANT_AGREEMENT"
    assert summary.policy["mdr_bps"] == policy.mdr_bps
    assert summary.policy["gst_on_fee_bps"] == policy.gst_on_fee_bps
    assert summary.policy["tolerance_paise"] == policy.tolerance_paise
    assert summary.policy["policy_fingerprint"] == policy.fingerprint
    assert "Razorpay published pricing" in str(summary.policy["notice"])
    # Every line item repeats the rates that produced it.
    assert all(item.contractual_mdr_bps == policy.mdr_bps for item in summary.items)
    assert all(item.contractual_gst_bps == policy.gst_on_fee_bps for item in summary.items)


def test_fee_audit_declares_truncated_line_items(tmp_path: Path) -> None:
    """A partial page of items is declared, never presented as the whole audit."""
    settings = Settings(db_path=tmp_path / "truncated.db", _env_file=None)
    app = create_app(settings)

    with TestClient(app):
        run_res = execute_run(
            inputs_dir=_dev_inputs(), database=app.state.db, mode="rules-only", force=True
        )
        summary = audit_run_fees(app.state.db, run_res.run_id, policy=resolve_fee_policy(settings))

    assert summary.audited_records_count > 0
    assert summary.items_returned_count == len(summary.items)
    assert summary.items_truncated == (summary.audited_records_count > summary.items_returned_count)


def test_fee_audit_configuration_changes_the_policy(tmp_path: Path) -> None:
    """A deployment sets the rates; the response reports what it used."""
    configured = resolve_fee_policy(
        Settings(
            db_path=tmp_path / "configured.db",
            synthetic_mdr_bps=150,
            synthetic_gst_on_fee_bps=1800,
            synthetic_fee_tolerance_paise=0,
            _env_file=None,
        )
    )
    default = resolve_fee_policy(Settings(db_path=tmp_path / "default.db", _env_file=None))

    assert configured.mdr_bps == 150
    assert configured.tolerance_paise == 0
    assert default.mdr_bps == 200
    assert default.tolerance_paise == 50
    # A changed rate must change the fingerprint, so a figure produced under one
    # policy can never be attributed to another.
    assert configured.fingerprint != default.fingerprint


def test_fee_audit_endpoint_ignores_caller_supplied_rates(tmp_path: Path) -> None:
    """A client cannot dictate the basis of a reported leakage figure.

    Chunk 3C: `?mdr_bps=1&gst_bps=1` was previously honoured and produced a
    large fabricated leakage with every record flagged anomalous.
    """
    settings = Settings(db_path=tmp_path / "test_fee_api.db", _env_file=None)
    app = create_app(settings)

    with TestClient(app) as client:
        run_res = execute_run(
            inputs_dir=_dev_inputs(), database=app.state.db, mode="rules-only", force=True
        )
        assert run_res.run_id is not None

        honest = client.get(f"/api/v1/runs/{run_res.run_id}/fee-audit")
        spoofed = client.get(f"/api/v1/runs/{run_res.run_id}/fee-audit?mdr_bps=1&gst_bps=1")
        missing = client.get("/api/v1/runs/run-does-not-exist/fee-audit")

    assert honest.status_code == 200
    data = honest.json()
    assert data["run_id"] == run_res.run_id
    assert "total_gmv_paise" in data
    assert "net_leakage_paise" in data
    assert "items" in data
    assert data["policy"]["mdr_bps"] == resolve_fee_policy(settings).mdr_bps

    assert spoofed.status_code == 200
    assert spoofed.json()["net_leakage_paise"] == data["net_leakage_paise"]
    assert spoofed.json()["anomalous_records_count"] == data["anomalous_records_count"]
    assert spoofed.json()["policy"] == data["policy"]

    assert missing.status_code == 404
