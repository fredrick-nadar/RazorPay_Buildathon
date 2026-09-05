"""Unit tests for the truthful active-run evidence dossier."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.runs import execute_run


def test_executive_dossier_endpoint(tmp_path: Path) -> None:
    settings = Settings(db_path=tmp_path / "test_dossier.db")
    app = create_app(settings)

    repo_root = Path(__file__).resolve().parents[3]
    inputs_dir = repo_root / "datasets" / "dev" / "inputs"

    with TestClient(app) as client:
        # Run a batch first
        run_res = execute_run(
            inputs_dir=inputs_dir,
            database=app.state.db,
            mode="rules-only",
            force=True,
        )
        assert run_res.run_id is not None

        # Fetch dossier
        res = client.get(f"/api/v1/runs/{run_res.run_id}/dossier")
        assert res.status_code == 200
        data = res.json()

        assert data["run_id"] == run_res.run_id
        assert "dossier_digest" in data
        assert len(data["dossier_digest"]) == 64  # SHA-256 hex length
        assert "summary" in data
        assert "compliance" not in data
        assert "cryptographic_seal" not in data
        assert data["provenance"] == {
            "scope": "ACTIVE_RUN_RUNTIME",
            "data_classification": "SYNTHETIC_ONLY",
            "evaluator_labels_used": False,
            "external_audit_performed": False,
            "regulatory_certification": False,
            "money_representation": "SIGNED_INTEGER_PAISE",
            "source_rows_immutable": True,
            "source_manifest": {
                "manifest_present": False,
                "manifest_fingerprint": None,
                "contains_synthetic_demo": False,
                "production_eligible": False,
                "sources": [],
                "notice": "No intake revision manifest accompanies this synthetic dataset run.",
            },
            "notice": (
                "This dossier reports reconciliation evidence and internal consistency only; "
                "it is not an external audit or regulatory certification."
            ),
        }
        assert isinstance(data["cases"], list)
        assert isinstance(data["audit_trail"], list)
        metrics = data["runtime_metrics"]
        assert metrics["eligible_record_count"] == data["summary"]["eligible_record_count"]
        assert metrics["matched_record_count"] == data["summary"]["matched_record_count"]
        assert metrics["runtime_match_rate"] == data["summary"]["runtime_match_rate"]
        assert sum(metrics["case_status_counts"].values()) == data["cases_count"]
        assert sum(metrics["verifier_status_counts"].values()) == data["cases_count"]
        assert metrics["audit_event_count"] == len(data["audit_trail"])


def test_dossier_not_found(tmp_path: Path) -> None:
    settings = Settings(db_path=tmp_path / "test_dossier_404.db")
    app = create_app(settings)

    with TestClient(app) as client:
        res = client.get("/api/v1/runs/nonexistent-run-id/dossier")
        assert res.status_code == 404


def test_active_run_contract_is_empty_then_returns_latest_exact_run(tmp_path: Path) -> None:
    settings = Settings(db_path=tmp_path / "test_active_run.db")
    app = create_app(settings)
    inputs_dir = Path(__file__).resolve().parents[3] / "datasets" / "dev" / "inputs"

    with TestClient(app) as client:
        empty = client.get("/api/v1/runs/active")
        assert empty.status_code == 200
        assert empty.json() is None

        run_res = execute_run(
            inputs_dir=inputs_dir,
            database=app.state.db,
            mode="rules-only",
            force=True,
        )
        active = client.get("/api/v1/runs/active")
        exact = client.get(f"/api/v1/runs/{run_res.run_id}/summary")

        assert active.status_code == 200
        assert exact.status_code == 200
        assert active.json() == exact.json()
        assert active.json()["run_id"] == run_res.run_id
        assert active.json()["economic_output_hash"] == run_res.economic_output_hash
