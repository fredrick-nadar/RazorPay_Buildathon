"""CSV schema review, immutable staging, and reconciliation readiness tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.config import Settings
from app.importers.schema_mapping import analyze_csv
from app.importers.session_staging import load_manifest, resolve_session_dir
from app.main import create_app

PAYMENT_CSV = (
    "payment_id,order_id,status,currency,gross_amount,fee_amount,tax_amount,"
    "captured_at_utc,settlement_id\n"
    "pay_1,ord_1,CAPTURED,INR,100.00,2.00,0.36,2026-03-02T03:17:28Z,stl_1\n"
)


def _mapping_from_analysis(analysis: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"target_field": item["target_field"], "source_column": item["source_column"]}
        for item in analysis["mappings"]
    ]


def _settings(tmp_path: Path, name: str) -> Settings:
    return Settings(
        db_path=tmp_path / f"{name}.sqlite3",
        import_staging_root=tmp_path / "imports",
        _env_file=None,
    )


def test_analyze_known_aliases_without_ai(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "aliases")
    content = (
        "Transaction ID,Transaction Date,Value Date,Currency,Amount,Particulars,"
        "UTR Number,Masked Account\n"
        "bnk_1,2026-03-03T04:23:47Z,2026-03-03,INR,97.64,RAZORPAY,UTR_1,FP_1\n"
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/ingest/analyze-csv",
            json={"filename": "bank.csv", "content": content, "file_type": "bank_entries"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mapping_provider"] == "DETERMINISTIC"
    assert payload["missing_required_fields"] == []
    assert {item["target_field"] for item in payload["mappings"]} >= {
        "bank_entry_id",
        "posted_at_utc",
        "signed_amount",
        "account_fingerprint",
    }


def test_reviewed_commit_preserves_raw_file_and_is_idempotent(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "commit")
    with TestClient(create_app(settings)) as client:
        analysis = client.post(
            "/api/v1/ingest/analyze-csv",
            json={"filename": "payments.csv", "content": PAYMENT_CSV, "file_type": "payments"},
        ).json()
        request = {
            "filename": "payments.csv",
            "content": PAYMENT_CSV,
            "file_type": "payments",
            "session_id": "reviewed_commit",
            "mappings": _mapping_from_analysis(analysis),
        }
        first = client.post("/api/v1/ingest/commit-csv", json=request)
        second = client.post("/api/v1/ingest/commit-csv", json=request)

    assert first.status_code == 200
    assert first.json()["accepted_count"] == 1
    assert first.json()["quarantined_count"] == 0
    assert first.json()["reused"] is False
    assert second.json()["reused"] is True
    session = resolve_session_dir(settings, "reviewed_commit", create=False)
    assert (session / "payments.csv").read_text(encoding="utf-8").count("pay_1") == 1
    source_files = [
        session / row["raw_path"] for row in load_manifest(session)["revisions"].values()
    ]
    assert len(source_files) == 1
    assert source_files[0].read_text(encoding="utf-8") == PAYMENT_CSV


def test_new_revision_replaces_active_source_without_overwriting_history(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "revisions")
    replacement = PAYMENT_CSV.replace("pay_1", "pay_2").replace("ord_1", "ord_2")
    with TestClient(create_app(settings)) as client:
        responses = []
        for filename, content in (("first.csv", PAYMENT_CSV), ("second.csv", replacement)):
            analysis = client.post(
                "/api/v1/ingest/analyze-csv",
                json={"filename": filename, "content": content, "file_type": "payments"},
            ).json()
            responses.append(
                client.post(
                    "/api/v1/ingest/commit-csv",
                    json={
                        "filename": filename,
                        "content": content,
                        "file_type": "payments",
                        "session_id": "revision_session",
                        "mappings": _mapping_from_analysis(analysis),
                    },
                ).json()
            )
        status = client.get("/api/v1/ingest/sessions/revision_session/status").json()

    session = resolve_session_dir(settings, "revision_session", create=False)
    assert responses[1]["revision_number"] == 2
    assert responses[1]["replaced_revision_id"] == responses[0]["revision_id"]
    assert status["revision_counts"]["payments"] == 2
    assert status["active_sources"]["payments"]["revision_id"] == responses[1]["revision_id"]
    assert "pay_2" in (session / "payments.csv").read_text(encoding="utf-8")
    assert "pay_1" not in (session / "payments.csv").read_text(encoding="utf-8")
    revisions = load_manifest(session)["revisions"]
    assert len(revisions) == 2
    assert all((session / row["raw_path"]).is_file() for row in revisions.values())
    assert all((session / row["canonical_path"]).is_file() for row in revisions.values())


def test_session_status_survives_application_restart(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "restart")
    with TestClient(create_app(settings)) as client:
        analysis = client.post(
            "/api/v1/ingest/analyze-csv",
            json={"filename": "payments.csv", "content": PAYMENT_CSV, "file_type": "payments"},
        ).json()
        client.post(
            "/api/v1/ingest/commit-csv",
            json={
                "filename": "payments.csv",
                "content": PAYMENT_CSV,
                "file_type": "payments",
                "session_id": "durable_session",
                "mappings": _mapping_from_analysis(analysis),
            },
        )

    with TestClient(create_app(settings)) as restarted_client:
        response = restarted_client.get("/api/v1/ingest/sessions/durable_session/status")

    assert response.status_code == 200
    assert response.json()["active_sources"]["payments"]["accepted_count"] == 1
    assert response.json()["ready_source_groups"] == 0


def test_corrupted_revision_manifest_is_reported_instead_of_ignored(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "corrupt")
    session = resolve_session_dir(settings, "corrupt_session", create=True)
    (session / ".source-revisions.json").write_text("{not-json", encoding="utf-8")

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/ingest/sessions/corrupt_session/status")

    assert response.status_code == 409
    assert "corrupted" in response.json()["detail"]


def test_commit_never_invents_missing_required_values(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "missing")
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/ingest/commit-csv",
            json={
                "filename": "short.csv",
                "content": "Payment ID,Amount\npay_1,100.00\n",
                "file_type": "payments",
                "session_id": "missing_fields",
                "mappings": [
                    {"target_field": "payment_id", "source_column": "Payment ID"},
                    {"target_field": "gross_amount", "source_column": "Amount"},
                ],
            },
        )

    assert response.status_code == 400
    assert "Required fields are not mapped" in response.json()["detail"]


def test_incomplete_sources_do_not_start_reconciliation(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "readiness")
    with TestClient(create_app(settings)) as client:
        analysis = client.post(
            "/api/v1/ingest/analyze-csv",
            json={"filename": "payments.csv", "content": PAYMENT_CSV, "file_type": "payments"},
        ).json()
        client.post(
            "/api/v1/ingest/commit-csv",
            json={
                "filename": "payments.csv",
                "content": PAYMENT_CSV,
                "file_type": "payments",
                "session_id": "not_ready",
                "mappings": _mapping_from_analysis(analysis),
            },
        )
        response = client.post(
            "/api/v1/ingest/reconcile-session",
            json={"session_id": "not_ready", "mode": "rules-only"},
        )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "bank statement" in detail
    assert "merchant ledger" in detail
    assert "complete run" in detail


def test_three_source_session_runs_only_after_all_evidence_is_ready(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "complete")
    sources = {
        "payments": PAYMENT_CSV,
        "settlements": (
            "settlement_id,settled_at_utc,window_start_utc,window_end_utc,status,"
            "currency,gross_credit,fee_amount,tax_amount,adjustment_amount,net_amount,utr\n"
            "stl_1,2026-03-03T04:18:47Z,2026-03-02T00:00:00Z,"
            "2026-03-03T00:00:00Z,PROCESSED,INR,100.00,2.00,0.36,0.00,97.64,UTR_1\n"
        ),
        "bank_entries": (
            "bank_entry_id,posted_at_utc,value_date,currency,signed_amount,narration,utr,"
            "account_fingerprint\n"
            "bnk_1,2026-03-03T04:23:47Z,2026-03-03,INR,97.64,RAZORPAY,UTR_1,FP_1\n"
        ),
        "ledger_entries": (
            "ledger_entry_id,account_code,accounting_date,currency,signed_amount,"
            "source_reference,source_type,description,entry_origin\n"
            "led_1,1100-BANK,2026-03-03,INR,97.64,pay_1,PAYMENT,Settlement,IMPORTED\n"
        ),
    }
    with TestClient(create_app(settings)) as client:
        for file_type, content in sources.items():
            analysis = client.post(
                "/api/v1/ingest/analyze-csv",
                json={
                    "filename": f"{file_type}.csv",
                    "content": content,
                    "file_type": file_type,
                },
            ).json()
            committed = client.post(
                "/api/v1/ingest/commit-csv",
                json={
                    "filename": f"{file_type}.csv",
                    "content": content,
                    "file_type": file_type,
                    "session_id": "complete_session",
                    "mappings": _mapping_from_analysis(analysis),
                },
            )
            assert committed.status_code == 200

        response = client.post(
            "/api/v1/ingest/reconcile-session",
            json={"session_id": "complete_session", "mode": "rules-only"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
    session = resolve_session_dir(settings, "complete_session", create=False)
    snapshots = list((session / ".runs").iterdir())
    assert len(snapshots) == 1
    assert (snapshots[0] / "refunds.csv").is_file()


def test_ocr_and_direct_upload_paths_are_not_exposed(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "disabled")
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/v1/ingest/upload-document", json={}).status_code == 404
        assert client.post("/api/v1/ingest/stream-extract", json={}).status_code == 404
        legacy = client.post("/api/v1/ingest/upload-csv", json={})
    assert legacy.status_code == 409


def test_groq_proposal_is_strict_and_cannot_escape_allowed_fields() -> None:
    calls: list[dict[str, Any]] = []

    def transport(method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        request = json.loads(body)
        calls.append(request)
        content = {
            "mappings": [
                {
                    "target_field": "payment_id",
                    "source_column": "Gateway Ref",
                    "confidence": "HIGH",
                    "reason": "Gateway reference identifies the payment.",
                },
                {
                    "target_field": "gross_amount",
                    "source_column": "Money",
                    "confidence": "HIGH",
                    "reason": "Money is the payment amount.",
                },
                {
                    "target_field": "invented_field",
                    "source_column": "Money",
                    "confidence": "HIGH",
                    "reason": "Must be ignored.",
                },
            ],
            "warnings": [],
        }
        return 200, json.dumps(
            {"choices": [{"message": {"content": json.dumps(content)}}]}
        ).encode()

    result = analyze_csv(
        content="Gateway Ref,Money\npay_1,100.00\n",
        document_type="payments",
        groq_api_key="test-key",
        transport=transport,
    )

    assert calls[0]["temperature"] == 0
    assert calls[0]["response_format"]["json_schema"]["strict"] is True
    assert {item["target_field"] for item in result["mappings"]} == {
        "payment_id",
        "gross_amount",
    }
    assert any("disallowed target" in warning for warning in result["warnings"])
