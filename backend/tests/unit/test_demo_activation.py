"""Demo generation history is not demo activation (REVIEW-002).

A persisted ``gateway_demo_evidence`` row proves a labelled bundle was
generated once. Whether that bundle is still the session's active evidence is a
separate question, answered by the current session manifest. These tests cover
the transitions between the two.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.routes_ingest import validate_canonical_rows
from app.config import Settings
from app.importers.demo_settlement import (
    DEMO_BUNDLE_SOURCES,
    GATEWAY_DEMO_SOURCES,
    derive_demo_activation,
)
from app.importers.session_staging import resolve_session_dir, stage_source_revision
from app.main import create_app
from app.persistence.gateway_imports import GatewayEntity, persist_gateway_snapshot

_SETTLEMENT_HEADER = (
    "settlement_id,settled_at_utc,window_start_utc,window_end_utc,status,currency,"
    "gross_credit,fee_amount,tax_amount,adjustment_amount,net_amount,utr\n"
)
_BANK_HEADER = (
    "bank_entry_id,posted_at_utc,value_date,currency,signed_amount,narration,utr,"
    "account_fingerprint\n"
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "activation.sqlite3",
        import_staging_root=tmp_path / "imports",
        _env_file=None,
    )


def _captured_payment(entity_id: str = "pay_demo_1") -> GatewayEntity:
    return GatewayEntity(
        entity_type="PAYMENT",
        entity_id=entity_id,
        payload={
            "id": entity_id,
            "order_id": "order_demo_1",
            "status": "captured",
            "amount": 10000,
            "fee": 236,
            "tax": 36,
            "currency": "INR",
            "created_at": 1772437000,
        },
        reconciliation_eligible=True,
        readiness_state="AWAITING_RAZORPAY_SETTLEMENT",
    )


def _supersede(
    settings: Settings,
    session_id: str,
    source_type: str,
    header: str,
    *,
    origin: str,
    import_id: str | None,
) -> None:
    """Activate a non-demo revision over a demo-staged source."""
    session_dir = resolve_session_dir(settings, session_id, create=False)
    assert session_dir is not None
    accepted, quarantined, _preview = validate_canonical_rows(header, source_type)  # type: ignore[arg-type]
    stage_source_revision(
        session_dir=session_dir,
        source_type=source_type,  # type: ignore[arg-type]
        original_filename=f"{origin.lower()}-{source_type}.csv",
        raw_content=header,
        canonical_csv=header,
        accepted_count=accepted,
        quarantined_count=quarantined,
        origin=origin,
        external_import_id=import_id,
    )


def test_derive_demo_activation_classifies_every_mix() -> None:
    """The pure rule: demo origin AND this import id, per source."""
    bundle_hash = hashlib.sha256(
        "".join(f"{source}.csv:{'a' * 64}" for source in sorted(DEMO_BUNDLE_SOURCES)).encode()
    ).hexdigest()
    full = {
        source: {
            "origin": "SYNTHETIC_DEMO",
            "external_import_id": "gwi-1",
            "canonical_sha256": "a" * 64,
            "demo_metadata": {
                "manifest_hash": bundle_hash,
                "derived_from_gateway_import": "gwi-1",
                "canonical_filename": f"{source}.csv",
                "provenance": "SYNTHETIC_DEMO",
            },
        }
        for source in DEMO_BUNDLE_SOURCES
    }
    assert derive_demo_activation(full, "gwi-1", bundle_hash)["activation_state"] == "ACTIVE"

    # Another import's demo bundle is never credited to this import.
    assert derive_demo_activation(full, "gwi-2", bundle_hash)["activation_state"] == "SUPERSEDED"

    partial = dict(full)
    partial["settlements"] = {"origin": "RAZORPAY_TEST_MODE", "external_import_id": "gwi-1"}
    outcome = derive_demo_activation(partial, "gwi-1", bundle_hash)
    assert outcome["activation_state"] == "PARTIALLY_ACTIVE"
    assert outcome["superseded_sources"] == ["settlements"]
    assert "payments" in outcome["active_demo_sources"]

    manual = {
        source: {"origin": "MANUAL_CSV", "external_import_id": None}
        for source in DEMO_BUNDLE_SOURCES
    }
    assert derive_demo_activation(manual, "gwi-1", bundle_hash)["activation_state"] == "SUPERSEDED"

    assert derive_demo_activation({}, "gwi-1", bundle_hash)["activation_state"] == "SUPERSEDED"
    full["payments"]["canonical_sha256"] = "b" * 64
    assert derive_demo_activation(full, "gwi-1", bundle_hash)["activation_state"] == "UNKNOWN"


@pytest.mark.parametrize(
    "damage", ["canonical", "raw", "missing_csv", "missing_manifest", "bundle_hash"]
)
def test_activation_verifies_content_not_only_origin(tmp_path: Path, damage: str) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        snapshot = persist_gateway_snapshot(
            app.state.db,
            provider="RAZORPAY",
            mode="TEST",
            credential_identifier="fixture-key",
            entities=[_captured_payment()],
        )
        url = f"/api/v1/razorpay/imports/{snapshot.import_id}"
        assert (
            client.post(
                url + "/generate-demo-evidence", json={"session_id": "hash_session"}
            ).status_code
            == 200
        )
        directory = resolve_session_dir(settings, "hash_session", create=False)
        manifest_path = directory / ".source-revisions.json"
        manifest = json.loads(manifest_path.read_text())
        revision = manifest["revisions"][manifest["active_by_type"]["payments"]]
        if damage == "missing_manifest":
            manifest_path.rename(directory / "saved-manifest.json")
        elif damage == "missing_csv":
            path = directory / revision["canonical_path"]
            path.rename(path.with_suffix(".saved"))
        elif damage == "bundle_hash":
            app.state.db.execute("UPDATE gateway_demo_evidence SET manifest_hash = ?", ("0" * 64,))
        else:
            (directory / revision[f"{damage}_path"]).write_bytes(b"corrupted synthetic fixture")
        result = client.get(url, params={"session_id": "hash_session"}).json()["demo_evidence"]
        assert result["activation_state"] == "UNKNOWN"
        assert result["active_demo_sources"] == []


def test_missing_fee_is_not_demo_eligible(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        payment = _captured_payment()
        payment.payload["fee"] = None
        snapshot = persist_gateway_snapshot(
            app.state.db,
            provider="RAZORPAY",
            mode="TEST",
            credential_identifier="fixture-key",
            entities=[payment],
        )
        url = f"/api/v1/razorpay/imports/{snapshot.import_id}"
        assert client.get(url).json()["demo_generation"]["eligible"] is False
        assert (
            client.post(
                url + "/generate-demo-evidence", json={"session_id": "invalid_demo"}
            ).status_code
            == 409
        )


def test_api_reimport_moves_a_demo_bundle_to_partially_active(tmp_path: Path) -> None:
    """The exact REVIEW-002 reproduction, now reported honestly."""
    settings = _settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        snapshot = persist_gateway_snapshot(
            app.state.db,
            provider="RAZORPAY",
            mode="TEST",
            credential_identifier="fixture-key",
            entities=[_captured_payment()],
        )
        generated = client.post(
            f"/api/v1/razorpay/imports/{snapshot.import_id}/generate-demo-evidence",
            json={"session_id": "act_session"},
        )
        assert generated.status_code == 200, generated.text
        evidence_id = generated.json()["evidence_id"]

        active = client.get(
            f"/api/v1/razorpay/imports/{snapshot.import_id}",
            params={"session_id": "act_session"},
        ).json()["demo_evidence"]
        assert active["activation_state"] == "ACTIVE"
        assert sorted(active["active_demo_sources"]) == sorted(GATEWAY_DEMO_SOURCES)
        assert active["scope"] == "GATEWAY_ONLY"
        assert active["superseded_sources"] == []

        # An unsettled API re-import supersedes only the settlement source.
        _supersede(
            settings,
            "act_session",
            "settlements",
            _SETTLEMENT_HEADER,
            origin="RAZORPAY_TEST_MODE",
            import_id=snapshot.import_id,
        )

        session = client.get("/api/v1/ingest/sessions/act_session/status").json()
        assert session["ready"] is False
        assert session["active_sources"]["settlements"]["origin"] == "RAZORPAY_TEST_MODE"

        partial = client.get(
            f"/api/v1/razorpay/imports/{snapshot.import_id}",
            params={"session_id": "act_session"},
        ).json()["demo_evidence"]
        # History is preserved and still queryable...
        assert partial["evidence_id"] == evidence_id
        assert partial["provenance"] == "SYNTHETIC_DEMO"
        # ...but it is no longer claimed to be fully active, and the synthetic
        # provenance still present in the session is not hidden either.
        assert partial["activation_state"] == "PARTIALLY_ACTIVE"
        assert partial["superseded_sources"] == ["settlements"]
        assert "payments" in partial["active_demo_sources"]


def test_manual_replacement_of_every_source_supersedes_the_bundle(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        snapshot = persist_gateway_snapshot(
            app.state.db,
            provider="RAZORPAY",
            mode="TEST",
            credential_identifier="fixture-key",
            entities=[_captured_payment()],
        )
        client.post(
            f"/api/v1/razorpay/imports/{snapshot.import_id}/generate-demo-evidence",
            json={"session_id": "sup_session"},
        )
        _supersede(
            settings,
            "sup_session",
            "settlements",
            _SETTLEMENT_HEADER,
            origin="MANUAL_CSV",
            import_id=None,
        )
        _supersede(
            settings,
            "sup_session",
            "bank_entries",
            _BANK_HEADER,
            origin="MANUAL_CSV",
            import_id=None,
        )
        outcome = client.get(
            f"/api/v1/razorpay/imports/{snapshot.import_id}",
            params={"session_id": "sup_session"},
        ).json()["demo_evidence"]
        assert outcome["activation_state"] == "PARTIALLY_ACTIVE"
        assert outcome["superseded_sources"] == ["settlements"]
        assert "bank_entries" not in outcome["expected_sources"]


def test_activation_survives_a_restart_and_stays_session_scoped(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        snapshot = persist_gateway_snapshot(
            app.state.db,
            provider="RAZORPAY",
            mode="TEST",
            credential_identifier="fixture-key",
            entities=[_captured_payment()],
        )
        client.post(
            f"/api/v1/razorpay/imports/{snapshot.import_id}/generate-demo-evidence",
            json={"session_id": "cold_session"},
        )

    # A second app over the same database and staging root: a cold read.
    restarted = create_app(_settings(tmp_path))
    with TestClient(restarted) as client:
        cold = client.get(
            f"/api/v1/razorpay/imports/{snapshot.import_id}",
            params={"session_id": "cold_session"},
        ).json()["demo_evidence"]
        assert cold["activation_state"] == "ACTIVE"

        # A different session cannot inherit this activation, or the record.
        other = client.get(
            f"/api/v1/razorpay/imports/{snapshot.import_id}",
            params={"session_id": "other_session"},
        ).json()
        assert other["demo_evidence"] is None


def test_history_without_a_staging_tree_is_never_reported_as_active(tmp_path: Path) -> None:
    """A demo row whose session directory is gone activates nothing."""
    settings = _settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        snapshot = persist_gateway_snapshot(
            app.state.db,
            provider="RAZORPAY",
            mode="TEST",
            credential_identifier="fixture-key",
            entities=[_captured_payment()],
        )
        client.post(
            f"/api/v1/razorpay/imports/{snapshot.import_id}/generate-demo-evidence",
            json={"session_id": "gone_session"},
        )
        session_dir = resolve_session_dir(settings, "gone_session", create=False)
        assert session_dir is not None
        manifest = session_dir / ".source-revisions.json"
        assert manifest.is_file()
        # Corrupt the manifest rather than deleting immutable evidence.
        manifest.write_text("{not json", encoding="utf-8")

        outcome = client.get(
            f"/api/v1/razorpay/imports/{snapshot.import_id}",
            params={"session_id": "gone_session"},
        )
        assert outcome.status_code == 200, outcome.text
        evidence = outcome.json()["demo_evidence"]
        assert evidence["activation_state"] == "UNKNOWN"
        assert evidence["active_demo_sources"] == []
