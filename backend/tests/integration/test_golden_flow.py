"""End-to-end golden flow and approval lifecycle integration tests (PRD §11, §16)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.enums import CaseStatus, CorrectionStatus
from app.main import create_app


def test_golden_flow_reconciliation_approval_and_audit(tmp_path: Path) -> None:
    db_path = tmp_path / "golden_flow.sqlite3"
    settings = Settings(db_path=db_path)
    app = create_app(settings)

    with TestClient(app) as client:
        # 1. Trigger run on dev dataset
        resp = client.post(
            "/api/v1/runs/reconcile",
            json={"dataset_profile": "dev", "mode": "rules-only", "force": True},
        )
        assert resp.status_code == 200
        run_data = resp.json()
        run_id = run_data["run_id"]
        assert run_data["status"] == "COMPLETED"
        assert run_data["summary"]["eligible_record_count"] >= 100

        # 2. Get run summary
        resp_sum = client.get(f"/api/v1/runs/{run_id}/summary")
        assert resp_sum.status_code == 200
        assert resp_sum.json()["run_id"] == run_id

        # 3. List cases
        resp_cases = client.get(f"/api/v1/runs/{run_id}/cases")
        assert resp_cases.status_code == 200
        cases = resp_cases.json()
        assert len(cases) > 0

        # Filter approval required cases
        appr_cases = [c for c in cases if c["status"] == CaseStatus.APPROVAL_REQUIRED.value]
        assert len(appr_cases) > 0

        target_case = appr_cases[0]
        case_id = target_case["case_id"]

        # 4. Get case workspace details
        resp_detail = client.get(f"/api/v1/cases/{case_id}")
        assert resp_detail.status_code == 200
        detail = resp_detail.json()
        assert detail["case"]["case_id"] == case_id
        assert detail["proof"] is not None
        assert detail["proof"]["verifier_status"] == "PASS"
        assert detail["dry_run"] is not None
        assert detail["dry_run"]["status"] == "DRAFT"

        # 5. Approve case, naming the exact proof that was just reviewed.
        proof_id = detail["proof"]["proof_id"]
        resp_appr = client.post(
            f"/api/v1/cases/{case_id}/approve",
            json={
                "proof_id": proof_id,
                "run_id": run_id,
                "reviewer_id": "rev-controller-alice",
                "notes": "Verified source UTR and duplicate entry.",
            },
        )
        assert resp_appr.status_code == 200
        appr_result = resp_appr.json()
        assert appr_result["status"] == CorrectionStatus.SIMULATED_APPLIED.value
        assert appr_result["reused"] is False
        assert appr_result["delta_paise"] != 0

        # 6. Idempotent re-approval
        resp_appr2 = client.post(
            f"/api/v1/cases/{case_id}/approve",
            json={
                "proof_id": proof_id,
                "run_id": run_id,
                "reviewer_id": "rev-controller-alice",
                "notes": "Re-run check",
            },
        )
        assert resp_appr2.status_code == 200
        assert resp_appr2.json()["reused"] is True
        assert resp_appr2.json()["correction_id"] == appr_result["correction_id"]

        # 6b. A decision naming a proof that is not current is refused outright
        # rather than retargeted onto whatever proof happens to be latest.
        resp_spoof = client.post(
            f"/api/v1/cases/{case_id}/approve",
            json={
                "proof_id": "proof-never-reviewed",
                "run_id": run_id,
                "reviewer_id": "rev-controller-alice",
            },
        )
        assert resp_spoof.status_code == 409
        assert resp_spoof.json()["detail"] == "PROOF_SUPERSEDED"

        # 7. Check case status transitioned
        resp_detail2 = client.get(f"/api/v1/cases/{case_id}")
        assert resp_detail2.status_code == 200
        assert resp_detail2.json()["case"]["status"] == CaseStatus.SIMULATED_APPLIED.value
        assert resp_detail2.json()["simulated_correction"] is not None

        # 8. Check audit trail
        resp_audit = client.get(f"/api/v1/cases/{case_id}/audit")
        assert resp_audit.status_code == 200
        audit_events = resp_audit.json()
        assert len(audit_events) >= 2
        actions = [e["action"] for e in audit_events]
        assert "APPROVAL_SUBMITTED" in actions
        assert "SIMULATED_CORRECTION_APPLIED" in actions

        # 9. Handle unresolvable/ambiguous case
        unres_cases = [c for c in cases if c["status"] == CaseStatus.UNRESOLVED.value]
        if unres_cases:
            unres_id = unres_cases[0]["case_id"]
            unres_detail = client.get(f"/api/v1/cases/{unres_id}?run_id={run_id}").json()
            unres_proof = unres_detail["proof"]

            # Approval must fail. An ambiguous case carries no verified proof to
            # authorize, so naming one is refused as a proof-identity error;
            # a case that does carry a non-PASS proof is refused by the
            # authority rules instead.
            resp_fail = client.post(
                f"/api/v1/cases/{unres_id}/approve",
                json={
                    "proof_id": unres_proof["proof_id"] if unres_proof else "proof-absent",
                    "run_id": run_id,
                    "reviewer_id": "rev-bob",
                },
            )
            assert resp_fail.status_code in (400, 409)

            # An already-unresolved case has no pending authority transition.
            resp_rej = client.post(
                f"/api/v1/cases/{unres_id}/reject",
                json={
                    "proof_id": unres_proof["proof_id"] if unres_proof else "none",
                    "run_id": run_id,
                    "reviewer_id": "rev-bob",
                    "notes": "Confirmed ambiguity: leaving open",
                },
            )
            assert resp_rej.status_code == 409
            assert resp_rej.json()["detail"] == "AUTHORITY_ALREADY_DECIDED"
