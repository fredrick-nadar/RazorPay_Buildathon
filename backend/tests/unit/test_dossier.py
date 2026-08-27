"""Unit tests for Executive Audit Dossier generation."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.runs import execute_run


def test_executive_dossier_endpoint(tmp_path: Path) -> None:
    settings = Settings(ARGUS_DB_PATH=str(tmp_path / "test_dossier.db"))
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
        assert "cryptographic_seal" in data
        assert len(data["cryptographic_seal"]) == 64  # SHA-256 hex length
        assert "summary" in data
        assert "compliance" in data
        assert data["compliance"]["integer_precision"] == "Signed Integer Paise (0 floats)"
        assert isinstance(data["cases"], list)
        assert isinstance(data["audit_trail"], list)


def test_dossier_not_found(tmp_path: Path) -> None:
    settings = Settings(ARGUS_DB_PATH=str(tmp_path / "test_dossier_404.db"))
    app = create_app(settings)

    with TestClient(app) as client:
        res = client.get("/api/v1/runs/nonexistent-run-id/dossier")
        assert res.status_code == 404
