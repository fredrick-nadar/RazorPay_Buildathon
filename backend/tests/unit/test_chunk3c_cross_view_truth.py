"""Chunk 3C: cross-view identity, fail-closed scoping, and authority binding.

Every test here runs against an isolated temporary SQLite database and makes no
network request of any kind.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.audit.service import get_audit_trail, record_audit_event
from app.config import Settings
from app.corrections.application import ProofIdentityError, apply_simulated_correction
from app.domain.enums import ActorType, ApprovalDecision
from app.graph.provenance import resolve_case_evidence_provenance
from app.main import create_app
from app.persistence.database import Database


def _dev_inputs() -> Path:
    return Path(__file__).resolve().parents[3] / "datasets" / "dev" / "inputs"


def _client(tmp_path: Path, name: str) -> TestClient:
    return TestClient(create_app(Settings(db_path=tmp_path / name, _env_file=None)))


def _reconcile(client: TestClient) -> str:
    response = client.post(
        "/api/v1/runs/reconcile",
        json={"dataset_profile": "dev", "mode": "rules-only", "force": True},
    )
    assert response.status_code == 200
    return str(response.json()["run_id"])


def _first_case(client: TestClient, run_id: str, status: str) -> dict[str, Any]:
    cases = client.get(f"/api/v1/runs/{run_id}/cases?status={status}").json()
    assert cases, f"fixture has no {status} case"
    return dict(cases[0])


# --------------------------------------------------------------------------- #
# Fail-closed run and case scoping (3C-2, 3C-14)                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "path",
    ["summary", "cases", "matrix", "audit", "fee-audit", "dossier"],
)
def test_unknown_run_is_404_not_empty(tmp_path: Path, path: str) -> None:
    """A missing run can never look like a legitimately empty run."""
    with _client(tmp_path, f"missing-{path}.sqlite3") as client:
        response = client.get(f"/api/v1/runs/run-does-not-exist/{path}")
    assert response.status_code == 404


def test_active_run_is_null_on_an_empty_database(tmp_path: Path) -> None:
    """A brand-new database reports no run, rather than an error or a guess.

    This is the backend half of the browser empty-state scenario.
    """
    with _client(tmp_path, "empty.sqlite3") as client:
        response = client.get("/api/v1/runs/active")
        listed = client.get("/api/v1/runs")

    assert response.status_code == 200
    assert response.json() is None
    assert listed.status_code == 200
    assert listed.json() == []


def test_case_detail_rejects_a_cross_run_selection(tmp_path: Path) -> None:
    """A case addressed to the wrong run is refused, not silently rendered."""
    with _client(tmp_path, "cross-run.sqlite3") as client:
        run_id = _reconcile(client)
        case = _first_case(client, run_id, "UNRESOLVED")
        case_id = case["case_id"]

        in_scope = client.get(f"/api/v1/cases/{case_id}?run_id={run_id}")
        cross_run = client.get(f"/api/v1/cases/{case_id}?run_id=run-someone-else")
        audit_cross_run = client.get(f"/api/v1/cases/{case_id}/audit?run_id=run-someone-else")
        unknown_case = client.get(f"/api/v1/cases/case-does-not-exist?run_id={run_id}")

    assert in_scope.status_code == 200
    assert in_scope.json()["case"]["run_id"] == run_id
    assert cross_run.status_code == 409
    assert cross_run.json()["detail"] == "CASE_RUN_MISMATCH"
    assert audit_cross_run.status_code == 409
    assert unknown_case.status_code == 404


# --------------------------------------------------------------------------- #
# Evidence provenance (3C-18)                                                 #
# --------------------------------------------------------------------------- #


def test_case_evidence_carries_source_revision_and_hash(tmp_path: Path) -> None:
    with _client(tmp_path, "provenance.sqlite3") as client:
        run_id = _reconcile(client)
        case = _first_case(client, run_id, "UNRESOLVED")
        detail = client.get(f"/api/v1/cases/{case['case_id']}?run_id={run_id}").json()

    evidence = detail["case"]["evidence"]
    assert evidence, "the fixture ambiguous case cites evidence"
    for item in evidence:
        assert item["resolution"] == "RESOLVED"
        assert item["run_id"] == run_id
        assert item["content_hash"]
        assert item["source_content_hash"] == item["content_hash"]
        assert item["revision_matches_source"] is True
        assert isinstance(item["source_row_number"], int)
        assert item["source_type"] == item["record_type"]
        assert item["source_state"]
        assert isinstance(item["amount_paise"], int)


def test_provenance_reports_an_unresolvable_citation_instead_of_dropping_it(
    tmp_path: Path,
) -> None:
    with _client(tmp_path, "unresolvable.sqlite3") as client:
        run_id = _reconcile(client)
        case_id = _first_case(client, run_id, "UNRESOLVED")["case_id"]
        db: Database = client.app.state.db
        db.execute(
            "INSERT INTO case_evidence (case_id, record_type, record_id, note) VALUES (?, ?, ?, ?)",
            (case_id, "PAYMENT", "pay_not_in_this_run", "injected citation"),
        )
        resolved = resolve_case_evidence_provenance(db, case_id, run_id)

    ghost = [item for item in resolved if item.record_id == "pay_not_in_this_run"]
    assert len(ghost) == 1
    assert ghost[0].resolution == "UNRESOLVED"
    assert ghost[0].resolution_reason == "NORMALIZED_RECORD_NOT_FOUND"
    assert ghost[0].content_hash is None


def test_provenance_is_scoped_to_the_run(tmp_path: Path) -> None:
    """An identically named record in another run never satisfies a citation."""
    with _client(tmp_path, "scoped-provenance.sqlite3") as client:
        run_id = _reconcile(client)
        case_id = _first_case(client, run_id, "UNRESOLVED")["case_id"]
        db: Database = client.app.state.db
        resolved = resolve_case_evidence_provenance(db, case_id, "run-a-different-one")

    assert resolved
    assert all(item.resolution == "UNRESOLVED" for item in resolved)


# --------------------------------------------------------------------------- #
# Audit ordering and scope (3C-19)                                            #
# --------------------------------------------------------------------------- #


def test_case_audit_is_scoped_and_ordered(tmp_path: Path) -> None:
    with _client(tmp_path, "audit-scope.sqlite3") as client:
        run_id = _reconcile(client)
        case_id = _first_case(client, run_id, "APPROVAL_REQUIRED")["case_id"]
        db: Database = client.app.state.db

        # An event on another case in the same run, and one on another run.
        other_case_id = _first_case(client, run_id, "UNRESOLVED")["case_id"]
        record_audit_event(
            db=db,
            actor=ActorType.SYSTEM,
            action="OTHER_CASE_EVENT",
            payload={},
            case_id=other_case_id,
            run_id=run_id,
        )
        record_audit_event(
            db=db,
            actor=ActorType.SYSTEM,
            action="OTHER_RUN_EVENT",
            payload={},
            case_id=case_id,
            run_id="run-elsewhere",
        )

        case_trail = client.get(f"/api/v1/cases/{case_id}/audit?run_id={run_id}").json()
        run_trail = client.get(f"/api/v1/runs/{run_id}/audit").json()

    actions = [event["action"] for event in case_trail]
    assert "OTHER_CASE_EVENT" not in actions
    # Passing both ids narrows to this case WITHIN this run; it used to widen
    # to `case_id = ? OR run_id = ?` and return the whole run's trail.
    assert "OTHER_RUN_EVENT" not in actions
    assert all(event["case_id"] == case_id for event in case_trail)

    sequences = [event["sequence"] for event in case_trail]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == len(sequences)
    run_sequences = [event["sequence"] for event in run_trail]
    assert run_sequences == sorted(run_sequences)
    assert all(event["digest"] for event in run_trail)


def test_get_audit_trail_intersects_case_and_run(tmp_path: Path) -> None:
    db = Database(tmp_path / "trail.sqlite3")
    try:
        record_audit_event(
            db=db, actor=ActorType.SYSTEM, action="A", payload={}, case_id="c1", run_id="r1"
        )
        record_audit_event(
            db=db, actor=ActorType.SYSTEM, action="B", payload={}, case_id="c2", run_id="r1"
        )
        record_audit_event(
            db=db, actor=ActorType.SYSTEM, action="C", payload={}, case_id="c1", run_id="r2"
        )
        both = [event.action for event in get_audit_trail(db=db, case_id="c1", run_id="r1")]
        by_run = [event.action for event in get_audit_trail(db=db, run_id="r1")]
        by_case = [event.action for event in get_audit_trail(db=db, case_id="c1")]
    finally:
        db.close()

    assert both == ["A"]
    assert by_run == ["A", "B"]
    assert by_case == ["A", "C"]


# --------------------------------------------------------------------------- #
# Human authority binding and idempotency (3C-10, 3C-11, 3C-12)               #
# --------------------------------------------------------------------------- #


def test_approval_requires_the_reviewed_proof(tmp_path: Path) -> None:
    """A human cannot authorize a proof they did not review."""
    with _client(tmp_path, "authority.sqlite3") as client:
        run_id = _reconcile(client)
        case_id = _first_case(client, run_id, "APPROVAL_REQUIRED")["case_id"]
        detail = client.get(f"/api/v1/cases/{case_id}?run_id={run_id}").json()
        proof_id = detail["proof"]["proof_id"]

        missing_proof = client.post(
            f"/api/v1/cases/{case_id}/approve",
            json={"reviewer_id": "test", "notes": "no proof field"},
        )
        wrong_proof = client.post(
            f"/api/v1/cases/{case_id}/approve",
            json={
                "proof_id": "proof-TOTALLY-BOGUS",
                "run_id": run_id,
                "reviewer_id": "test",
                "notes": "spoof",
            },
        )
        wrong_run = client.post(
            f"/api/v1/cases/{case_id}/approve",
            json={"proof_id": proof_id, "run_id": "run-elsewhere", "reviewer_id": "test"},
        )
        still_pending = client.get(f"/api/v1/cases/{case_id}?run_id={run_id}").json()

    # proof_id is now a required field, so an old client is refused outright.
    assert missing_proof.status_code == 422
    assert wrong_proof.status_code == 409
    assert wrong_proof.json()["detail"] == "PROOF_SUPERSEDED"
    assert wrong_run.status_code == 409
    # Nothing was written by any refused decision.
    assert still_pending["case"]["status"] == "APPROVAL_REQUIRED"
    assert still_pending["simulated_correction"] is None
    assert still_pending["approvals"] == []


def test_repeated_approval_applies_exactly_one_correction(tmp_path: Path) -> None:
    """Repeated clicks, refreshes and retries stay idempotent."""
    with _client(tmp_path, "idempotent.sqlite3") as client:
        run_id = _reconcile(client)
        case = _first_case(client, run_id, "APPROVAL_REQUIRED")
        case_id = case["case_id"]
        detail = client.get(f"/api/v1/cases/{case_id}?run_id={run_id}").json()
        proof_id = detail["proof"]["proof_id"]
        dry_run = detail["dry_run"]
        body = {"proof_id": proof_id, "run_id": run_id, "reviewer_id": "test", "notes": "first"}

        first = client.post(f"/api/v1/cases/{case_id}/approve", json=body)
        second = client.post(
            f"/api/v1/cases/{case_id}/approve", json={**body, "notes": "duplicate click"}
        )
        third = client.post(
            f"/api/v1/cases/{case_id}/approve", json={**body, "notes": "retry after refresh"}
        )
        after = client.get(f"/api/v1/cases/{case_id}?run_id={run_id}").json()
        db: Database = client.app.state.db
        applied = db.query_all("SELECT * FROM simulated_corrections WHERE case_id = ?", (case_id,))
        approvals = db.query_all("SELECT * FROM approvals WHERE case_id = ?", (case_id,))

    assert first.status_code == 200
    assert first.json()["reused"] is False
    for response in (second, third):
        assert response.status_code == 200
        assert response.json()["reused"] is True
        assert response.json()["correction_id"] == first.json()["correction_id"]

    assert len(applied) == 1
    assert len(approvals) == 1
    assert after["case"]["status"] == "SIMULATED_APPLIED"
    assert after["simulated_correction"]["correction_id"] == first.json()["correction_id"]
    # The applied delta is exactly the previewed delta, in signed integer paise.
    assert after["simulated_correction"]["delta_paise"] == dry_run["proposed_delta_paise"]
    assert isinstance(after["simulated_correction"]["delta_paise"], int)


def test_concurrent_application_reuses_the_single_entry(tmp_path: Path) -> None:
    """Two independent SQLite connections serialize one authority transition."""
    path = tmp_path / "race.sqlite3"
    with _client(tmp_path, path.name) as client:
        run_id = _reconcile(client)
        case_id = _first_case(client, run_id, "APPROVAL_REQUIRED")["case_id"]
        detail = client.get(f"/api/v1/cases/{case_id}?run_id={run_id}").json()
        proof_id = detail["proof"]["proof_id"]

    first_db = Database(path)
    second_db = Database(path)
    start = Barrier(2)

    def decide(db: Database, reviewer: str) -> dict[str, Any]:
        start.wait()
        return apply_simulated_correction(
            db=db,
            case_id=case_id,
            reviewer_id=reviewer,
            action=ApprovalDecision.APPROVED,
            expected_proof_id=proof_id,
            expected_run_id=run_id,
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(lambda pair: decide(*pair), ((first_db, "first"), (second_db, "second")))
            )
        applied = first_db.query_all(
            "SELECT * FROM simulated_corrections WHERE case_id = ?", (case_id,)
        )
        approvals = first_db.query_all("SELECT * FROM approvals WHERE case_id = ?", (case_id,))
    finally:
        first_db.close()
        second_db.close()

    assert sorted(result["reused"] for result in results) == [False, True]
    assert len({result["correction_id"] for result in results}) == 1
    assert len(applied) == 1
    assert len(approvals) == 1


def test_rejection_is_also_bound_to_the_reviewed_proof(tmp_path: Path) -> None:
    with _client(tmp_path, "reject.sqlite3") as client:
        run_id = _reconcile(client)
        case_id = _first_case(client, run_id, "APPROVAL_REQUIRED")["case_id"]
        db: Database = client.app.state.db

        with pytest.raises(ProofIdentityError):
            apply_simulated_correction(
                db=db,
                case_id=case_id,
                reviewer_id="test",
                action=ApprovalDecision.REJECTED,
                expected_proof_id="proof-not-current",
                expected_run_id=run_id,
            )
        untouched = client.get(f"/api/v1/cases/{case_id}?run_id={run_id}").json()

    assert untouched["case"]["status"] == "APPROVAL_REQUIRED"
    assert untouched["approvals"] == []


def test_rejection_is_idempotent_and_cannot_contradict_application(tmp_path: Path) -> None:
    with _client(tmp_path, "authority-transitions.sqlite3") as client:
        run_id = _reconcile(client)
        cases = client.get(f"/api/v1/runs/{run_id}/cases?status=APPROVAL_REQUIRED").json()
        rejected_case, applied_case = cases[:2]

        rejected_detail = client.get(
            f"/api/v1/cases/{rejected_case['case_id']}?run_id={run_id}"
        ).json()
        rejected_body = {
            "proof_id": rejected_detail["proof"]["proof_id"],
            "run_id": run_id,
            "reviewer_id": "reviewer",
        }
        first_reject = client.post(
            f"/api/v1/cases/{rejected_case['case_id']}/reject", json=rejected_body
        )
        repeated_reject = client.post(
            f"/api/v1/cases/{rejected_case['case_id']}/reject", json=rejected_body
        )
        contradictory_approve = client.post(
            f"/api/v1/cases/{rejected_case['case_id']}/approve", json=rejected_body
        )

        applied_detail = client.get(
            f"/api/v1/cases/{applied_case['case_id']}?run_id={run_id}"
        ).json()
        applied_body = {
            "proof_id": applied_detail["proof"]["proof_id"],
            "run_id": run_id,
            "reviewer_id": "reviewer",
        }
        approved = client.post(
            f"/api/v1/cases/{applied_case['case_id']}/approve", json=applied_body
        )
        contradictory_reject = client.post(
            f"/api/v1/cases/{applied_case['case_id']}/reject", json=applied_body
        )

        rejected_approvals = client.app.state.db.query_all(
            "SELECT * FROM approvals WHERE case_id = ?", (rejected_case["case_id"],)
        )
        applied_rows = client.app.state.db.query_all(
            "SELECT * FROM simulated_corrections WHERE case_id = ?", (applied_case["case_id"],)
        )

    assert first_reject.status_code == 200
    assert first_reject.json()["reused"] is False
    assert repeated_reject.status_code == 200
    assert repeated_reject.json()["reused"] is True
    assert repeated_reject.json()["approval_id"] == first_reject.json()["approval_id"]
    assert contradictory_approve.status_code == 409
    assert contradictory_approve.json()["detail"] == "AUTHORITY_ALREADY_DECIDED"
    assert approved.status_code == 200
    assert contradictory_reject.status_code == 409
    assert contradictory_reject.json()["detail"] == "AUTHORITY_ALREADY_DECIDED"
    assert len(rejected_approvals) == 1
    assert len(applied_rows) == 1


def test_authority_failures_never_leak_raw_exception_text(tmp_path: Path) -> None:
    """A refused decision returns a stable code, not internal detail."""
    with _client(tmp_path, "safe-errors.sqlite3") as client:
        run_id = _reconcile(client)
        unresolved = _first_case(client, run_id, "UNRESOLVED")
        response = client.post(
            f"/api/v1/cases/{unresolved['case_id']}/approve",
            json={"proof_id": "proof-anything", "run_id": run_id, "reviewer_id": "test"},
        )

    # An ambiguous case has no current proof, so the decision is refused as a
    # proof-identity failure rather than executing against nothing.
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail == "PROOF_SUPERSEDED"
    assert "Traceback" not in detail
    assert "sqlite3" not in detail


# --------------------------------------------------------------------------- #
# Integration status (3C-4, 3C-5)                                             #
# --------------------------------------------------------------------------- #


def test_integration_status_separates_configured_from_reachable(tmp_path: Path) -> None:
    """A plain status read makes no outbound request and claims no reachability."""
    settings = Settings(
        db_path=tmp_path / "status.sqlite3",
        razorpay_key_id=None,
        razorpay_key_secret=None,
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/status/integrations")
        probed = client.get("/api/v1/status/integrations?probe=database")
        remembered = client.get("/api/v1/status/integrations")

    assert response.status_code == 200
    body = response.json()
    assert body["probed"] == []
    by_name = {entry["name"]: entry for entry in body["integrations"]}

    # Database is always configured but is not called REACHABLE unprobed.
    assert by_name["database"]["configured"] is True
    assert by_name["database"]["state"] == "CONFIGURED"
    assert by_name["database"]["last_checked_utc"] is None
    assert by_name["database"]["probe_performed"] is False

    # No credentials means NOT_CONFIGURED, never a green light.
    assert by_name["razorpay"]["configured"] is False
    assert by_name["razorpay"]["state"] == "NOT_CONFIGURED"
    assert by_name["razorpay"]["detail"]["key_id_masked"] is None
    assert by_name["razorpay"]["last_checked_utc"] is None

    # The investigator is reported as configuration only and never probed.
    assert by_name["investigator"]["probe_performed"] is False
    assert by_name["investigator"]["state"] in {"CONFIGURED", "NOT_CONFIGURED"}

    probed_body = probed.json()
    assert probed_body["probed"] == ["database"]
    probed_db = {entry["name"]: entry for entry in probed_body["integrations"]}["database"]
    assert probed_db["state"] == "REACHABLE"
    assert probed_db["probe_ok"] is True
    assert probed_db["last_checked_utc"]
    remembered_db = {entry["name"]: entry for entry in remembered.json()["integrations"]}[
        "database"
    ]
    assert remembered_db["state"] == "REACHABLE"
    assert remembered_db["last_checked_utc"] == probed_db["last_checked_utc"]
    # An unprobeable integration stays unprobed even when named.
    assert {entry["name"]: entry for entry in probed_body["integrations"]}["investigator"][
        "probe_performed"
    ] is False


def test_integration_status_never_serializes_a_secret(tmp_path: Path) -> None:
    settings = Settings(
        db_path=tmp_path / "masked.sqlite3",
        razorpay_key_id="synthetic-key-id-fixture",
        razorpay_key_secret="super-secret-value",  # noqa: S106 - synthetic fixture
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        body = client.get("/api/v1/status/integrations").text

    assert "super-secret-value" not in body
    assert "syntheti...ture" in body
    # Configured credentials still do not assert reachability.
    assert '"state": "CONFIGURED"' in body or '"state":"CONFIGURED"' in body


# --------------------------------------------------------------------------- #
# Home/chat run scoping (3C-13)                                               #
# --------------------------------------------------------------------------- #


def test_chat_context_is_scoped_to_the_selected_run(tmp_path: Path) -> None:
    from app.voice.conversational_agent import _gather_live_financial_context

    with _client(tmp_path, "chat-scope.sqlite3") as client:
        run_id = _reconcile(client)
        db: Database = client.app.state.db

        selected = _gather_live_financial_context(db, scope_run_id=run_id)
        unknown = _gather_live_financial_context(db, scope_run_id="run-nope")
        implicit = _gather_live_financial_context(db)

    assert selected["summary"]["active_run_id"] == run_id
    assert selected["summary"]["scope"] == "SELECTED_RUN"
    assert selected["summary"]["total_cases"] > 0
    assert [entry["run_id"] for entry in selected["runs"]] == [run_id]

    # An unknown selection scopes to nothing; it never falls back to the newest
    # run and then presents that run's totals as the selected run's.
    assert unknown["summary"]["scope"] == "SELECTED_RUN_NOT_FOUND"
    assert unknown["summary"]["active_run_id"] is None
    assert unknown["summary"]["total_cases"] == 0
    assert unknown["summary"]["deterministic_match_rate"] == "NOT_REPORTED_BY_THIS_RUN"

    assert implicit["summary"]["scope"] == "LATEST_RUN"


def test_chat_context_never_invents_a_match_rate(tmp_path: Path) -> None:
    """A run with no reported rate says so; it does not derive one."""
    from app.voice.conversational_agent import _gather_live_financial_context

    with _client(tmp_path, "no-rate.sqlite3") as client:
        run_id = _reconcile(client)
        db: Database = client.app.state.db
        db.execute(
            "UPDATE runs SET summary_json = ? WHERE run_id = ?",
            ('{"mode": "rules-only"}', run_id),
        )
        context = _gather_live_financial_context(db, scope_run_id=run_id)

    summary = context["summary"]
    assert summary["deterministic_match_rate"] == "NOT_REPORTED_BY_THIS_RUN"
    assert summary["match_rate_numerator"] is None
    assert summary["match_rate_denominator"] is None
    # Source rows are still present, so the old fallback would have produced a
    # percentage here from (source_rows - cases) / source_rows.
    assert summary["total_input_records"] > 0


def test_chat_preserves_a_reported_zero_variance(tmp_path: Path) -> None:
    """Zero is a financial value, not a signal to substitute another field."""
    from app.voice.conversational_agent import _gather_live_financial_context

    with _client(tmp_path, "zero-variance.sqlite3") as client:
        run_id = _reconcile(client)
        db: Database = client.app.state.db
        db.execute(
            "UPDATE cases SET variance_paise = 0, proposed_delta_paise = 99999 WHERE run_id = ?",
            (run_id,),
        )
        context = _gather_live_financial_context(db, scope_run_id=run_id)

    assert context["summary"]["total_abs_case_variance_paise"] == 0
    assert all(case["variance_inr"] == "₹0.00" for case in context["recon_cases"])


def test_chat_refuses_a_case_from_another_run(tmp_path: Path) -> None:
    with _client(tmp_path, "chat-case.sqlite3") as client:
        run_id = _reconcile(client)
        case_id = _first_case(client, run_id, "UNRESOLVED")["case_id"]

        in_scope = client.post(
            "/api/v1/chat/message",
            json={
                "message": "what is the match rate",
                "page_context": {"active_run_id": run_id, "case_id": case_id},
            },
        )
        cross_run = client.post(
            "/api/v1/chat/message",
            json={
                "message": "what is the match rate",
                "page_context": {"active_run_id": "run-elsewhere", "case_id": case_id},
            },
        )

    assert in_scope.status_code == 200
    assert in_scope.json()["context_summary"]["active_run_id"] == run_id

    assert cross_run.status_code == 200
    body = cross_run.json()
    assert body["context_summary"]["scope"] == "SELECTED_RUN_NOT_FOUND"
    assert body["context_summary"]["active_run_id"] is None
    assert body["context_summary"]["total_cases"] == 0
    # With no run in scope the deterministic reply reports nothing to describe.
    assert "no reconciliation run in scope" in body["reply"].lower()
