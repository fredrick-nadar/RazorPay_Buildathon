"""Seed a LOCAL, isolated import-lifecycle fixture for end-to-end tests.

This script makes NO network calls and NO Razorpay requests of any kind. It is
not a Test Mode seeder: it writes synthetic rows straight into an isolated
SQLite database and staging tree, then prints the resulting identifiers as JSON
so a Playwright run can address them.

It refuses to touch a database that already exists, so it can never overwrite a
development or demo database.

Sessions created:

  e2e_demo_active       captured payment, full labelled demo bundle staged
  e2e_demo_superseded   same, with the settlement source replaced afterwards
  e2e_mixed_population  captured payment, failed payment, processed refund
  e2e_pending           captured payments, no settlement evidence at all
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.api.routes_ingest import validate_canonical_rows  # noqa: E402
from app.config import Settings  # noqa: E402
from app.importers.demo_settlement import build_demo_evidence  # noqa: E402
from app.importers.session_staging import (  # noqa: E402
    resolve_session_dir,
    stage_source_revision,
)
from app.persistence.database import Database  # noqa: E402
from app.persistence.gateway_imports import (  # noqa: E402
    GatewayEntity,
    mark_gateway_import_staged,
    persist_gateway_snapshot,
    record_demo_evidence,
)
from app.runs import execute_run  # noqa: E402

_SETTLEMENT_HEADER = (
    "settlement_id,settled_at_utc,window_start_utc,window_end_utc,status,currency,"
    "gross_credit,fee_amount,tax_amount,adjustment_amount,net_amount,utr\n"
)


def _payment(
    index: int, *, status: str = "captured", complete: bool = True
) -> GatewayEntity:
    payload: dict[str, Any] = {
        "id": f"pay_e2e_{index:03d}",
        "order_id": f"order_e2e_{index:03d}",
        "status": status,
        "amount": 25000 + index,
        "currency": "INR",
        "created_at": 1772437000 + index * 60,
    }
    if complete:
        payload["fee"] = 590
        payload["tax"] = 90
    eligible = status == "captured" and complete
    return GatewayEntity(
        entity_type="PAYMENT",
        entity_id=payload["id"],
        payload=payload,
        reconciliation_eligible=eligible,
        exclusion_reason=None if eligible else "PAYMENT_NOT_CAPTURED",
        readiness_state=(
            "AWAITING_RAZORPAY_SETTLEMENT"
            if eligible
            else "NOT_RECONCILIATION_ELIGIBLE"
        ),
    )


def _refund(index: int) -> GatewayEntity:
    payload = {
        "id": f"rfnd_e2e_{index:03d}",
        "payment_id": f"pay_e2e_{index:03d}",
        "status": "processed",
        "amount": 1000,
        "currency": "INR",
        "created_at": 1772439000 + index,
    }
    return GatewayEntity(
        entity_type="REFUND",
        entity_id=payload["id"],
        payload=payload,
        reconciliation_eligible=True,
        readiness_state="AWAITING_RAZORPAY_SETTLEMENT",
    )


def _order(index: int) -> GatewayEntity:
    payload = {
        "id": f"order_e2e_{index:03d}",
        "status": "paid",
        "amount": 25000 + index,
    }
    return GatewayEntity(
        entity_type="ORDER",
        entity_id=payload["id"],
        payload=payload,
        reconciliation_eligible=False,
        exclusion_reason="ORDER_IS_NOT_A_PAYMENT",
    )


def _snapshot(db: Database, entities: list[GatewayEntity], label: str) -> str:
    result = persist_gateway_snapshot(
        db,
        provider="RAZORPAY",
        mode="TEST",
        credential_identifier=f"rzp_test_e2e_{label}",
        entities=entities,
    )
    return result.import_id


def _stage_demo_bundle(
    db: Database,
    settings: Settings,
    import_id: str,
    session_id: str,
    payments: list[Any],
) -> str:
    """Reproduce legacy full-demo sessions to test compatibility after upgrades."""
    bundle = build_demo_evidence(
        import_id=import_id, payments=payments, refunds=[], include_merchant_sources=True
    )
    session_dir = resolve_session_dir(settings, session_id, create=True)
    for filename, source_type in (
        ("payments.csv", "payments"),
        ("refunds.csv", "refunds"),
        ("settlements.csv", "settlements"),
        ("bank_entries.csv", "bank_entries"),
        ("ledger_entries.csv", "ledger_entries"),
    ):
        canonical = bundle["files"][filename]
        accepted, quarantined, _preview = validate_canonical_rows(
            canonical, source_type
        )
        if quarantined:
            raise SystemExit(f"fixture generation produced invalid {source_type} rows")
        stage_source_revision(
            session_dir=session_dir,
            source_type=source_type,
            original_filename=f"synthetic-demo-{import_id}-{filename}",
            raw_content=json.dumps(
                {
                    "provenance": "SYNTHETIC_DEMO",
                    "derived_from_gateway_import": import_id,
                    "manifest_hash": bundle["manifest_hash"],
                    "canonical_filename": filename,
                },
                sort_keys=True,
            ),
            canonical_csv=canonical,
            accepted_count=accepted,
            quarantined_count=quarantined,
            origin="SYNTHETIC_DEMO",
            external_import_id=import_id,
        )
    evidence_id, _reused = record_demo_evidence(
        db,
        import_id=import_id,
        session_id=session_id,
        manifest_hash=str(bundle["manifest_hash"]),
    )
    return evidence_id


def _stage_pending_payments(
    settings: Settings, import_id: str, session_id: str, count: int
) -> None:
    """Stage API-origin payments with no settlement evidence at all."""
    session_dir = resolve_session_dir(settings, session_id, create=True)
    header = (
        "payment_id,order_id,status,currency,gross_amount,fee_amount,tax_amount,"
        "captured_at_utc,settlement_id\n"
    )
    rows = "".join(
        f"pay_e2e_{index:03d},order_e2e_{index:03d},CAPTURED,INR,{250 + index}.00,"
        f"5.00,0.90,2026-02-28T09:00:{index:02d}Z,\n"
        for index in range(count)
    )
    canonical = header + rows
    accepted, quarantined, _preview = validate_canonical_rows(canonical, "payments")
    stage_source_revision(
        session_dir=session_dir,
        source_type="payments",
        original_filename=f"razorpay-payments-{import_id}.json",
        raw_content=json.dumps({"fixture": "pending-settlement"}, sort_keys=True),
        canonical_csv=canonical,
        accepted_count=accepted,
        quarantined_count=quarantined,
        origin="RAZORPAY_TEST_MODE",
        external_import_id=import_id,
    )


def _paise_text(paise: int) -> str:
    """Render integer paise as the decimal rupee string the importers parse."""
    sign = "-" if paise < 0 else ""
    absolute = abs(paise)
    return f"{sign}{absolute // 100}.{absolute % 100:02d}"


def _write_clean_inputs(root: Path) -> Path:
    """Write a fully linked five-source input set that raises no exceptions.

    The dev dataset deliberately injects discrepancies, so on its own it can
    never prove that a legitimate zero-exception run renders as a success
    rather than a broken view. This set satisfies every deterministic rule
    exactly: each payment's clearing entry equals its net, the settlement's
    net equals gross minus fee and tax, the bank credit equals that net under
    a shared UTR, and the settlement's bank entry books the same net.

    All amounts are computed in integer paise and only rendered as decimal
    text at the CSV boundary.
    """
    count = 6
    payments: list[dict[str, int | str]] = []
    gross_total = fee_total = tax_total = 0
    for index in range(count):
        gross = 50_000 + index * 1_000
        fee = 1_000 + index * 20
        tax = (fee * 1800 + 5000) // 10000
        gross_total += gross
        fee_total += fee
        tax_total += tax
        payments.append(
            {
                "payment_id": f"pay_clean_{index:03d}",
                "order_id": f"order_clean_{index:03d}",
                "gross": gross,
                "fee": fee,
                "tax": tax,
                "net": gross - fee - tax,
            }
        )

    settlement_id = "stl_clean_000"
    utr = "UTRCLEANE2E000001"
    net_total = gross_total - fee_total - tax_total

    inputs = root / "clean-inputs"
    inputs.mkdir(parents=True, exist_ok=True)

    (inputs / "payments.csv").write_text(
        "payment_id,order_id,status,currency,gross_amount,fee_amount,tax_amount,"
        "captured_at_utc,settlement_id\n"
        + "".join(
            f"{row['payment_id']},{row['order_id']},CAPTURED,INR,"
            f"{_paise_text(int(row['gross']))},{_paise_text(int(row['fee']))},"
            f"{_paise_text(int(row['tax']))},2026-03-02T0{index}:00:00Z,{settlement_id}\n"
            for index, row in enumerate(payments)
        ),
        encoding="utf-8",
    )

    # A clean run with no refunds at all: the header alone is a valid source.
    (inputs / "refunds.csv").write_text(
        "refund_id,payment_id,status,currency,refund_amount,created_at_utc,settlement_id\n",
        encoding="utf-8",
    )

    (inputs / "settlements.csv").write_text(
        "settlement_id,settled_at_utc,window_start_utc,window_end_utc,status,currency,"
        "gross_credit,fee_amount,tax_amount,adjustment_amount,net_amount,utr\n"
        f"{settlement_id},2026-03-03T04:00:00Z,2026-03-02T00:00:00Z,2026-03-03T00:00:00Z,"
        f"PROCESSED,INR,{_paise_text(gross_total)},{_paise_text(fee_total)},"
        f"{_paise_text(tax_total)},0.00,{_paise_text(net_total)},{utr}\n",
        encoding="utf-8",
    )

    (inputs / "bank_entries.csv").write_text(
        "bank_entry_id,posted_at_utc,value_date,currency,signed_amount,narration,utr,"
        "account_fingerprint\n"
        f"bnk_clean_000,2026-03-03T04:05:00Z,2026-03-03,INR,{_paise_text(net_total)},"
        f"NEFT CR {utr} SYNTHETIC CLEAN SETTLEMENT {settlement_id},{utr},FP-ARGUS-E2E-CLEAN\n",
        encoding="utf-8",
    )

    ledger_rows = "".join(
        f"led_clean_p{index:03d},2100-PAYMENTS-CLEARING,2026-03-02,INR,"
        f"{_paise_text(int(row['net']))},{row['payment_id']},PAYMENT,"
        f"Payment captured {row['payment_id']},IMPORTED\n"
        for index, row in enumerate(payments)
    )
    ledger_rows += (
        f"led_clean_s000,1100-BANK-OPERATING,2026-03-03,INR,{_paise_text(net_total)},"
        f"{settlement_id},SETTLEMENT,Settlement credit {settlement_id},IMPORTED\n"
    )
    (inputs / "ledger_entries.csv").write_text(
        "ledger_entry_id,account_code,accounting_date,currency,signed_amount,source_reference,"
        "source_type,description,entry_origin\n" + ledger_rows,
        encoding="utf-8",
    )
    return inputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", required=True, type=Path, help="Isolated SQLite path to create"
    )
    parser.add_argument(
        "--staging",
        required=True,
        type=Path,
        help="Isolated import staging root to create",
    )
    args = parser.parse_args()

    if args.db.exists():
        parser.error(f"refusing to reuse an existing database: {args.db}")

    args.db.parent.mkdir(parents=True, exist_ok=True)
    args.staging.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        db_path=args.db, import_staging_root=args.staging, _env_file=None
    )
    db = Database(args.db)
    fixture: dict[str, Any] = {}
    try:
        # 1. A fully active labelled demo bundle.
        payments = [_payment(0), _payment(1)]
        demo_import = _snapshot(db, [_order(0), _order(1), *payments], "demo")
        mark_gateway_import_staged(db, demo_import)
        payloads = [entity.payload for entity in payments]
        fixture["demo_active"] = {
            "session_id": "e2e_demo_active",
            "import_id": demo_import,
            "evidence_id": _stage_demo_bundle(
                db, settings, demo_import, "e2e_demo_active", payloads
            ),
        }

        # 2. The same bundle with its settlement source replaced afterwards.
        superseded_evidence = _stage_demo_bundle(
            db, settings, demo_import, "e2e_demo_superseded", payloads
        )
        session_dir = resolve_session_dir(settings, "e2e_demo_superseded", create=False)
        assert session_dir is not None
        accepted, quarantined, _preview = validate_canonical_rows(
            _SETTLEMENT_HEADER, "settlements"
        )
        stage_source_revision(
            session_dir=session_dir,
            source_type="settlements",
            original_filename=f"razorpay-settlements-{demo_import}.json",
            raw_content=json.dumps({"fixture": "unsettled-reimport"}, sort_keys=True),
            canonical_csv=_SETTLEMENT_HEADER,
            accepted_count=accepted,
            quarantined_count=quarantined,
            origin="RAZORPAY_TEST_MODE",
            external_import_id=demo_import,
        )
        fixture["demo_superseded"] = {
            "session_id": "e2e_demo_superseded",
            "import_id": demo_import,
            "evidence_id": superseded_evidence,
        }

        # 3. Mixed population: captured payment, failed payment, processed refund.
        mixed_import = _snapshot(
            db,
            [_payment(0), _payment(7, status="failed", complete=False), _refund(0)],
            "mixed",
        )
        mark_gateway_import_staged(db, mixed_import)
        _stage_pending_payments(settings, mixed_import, "e2e_mixed_population", 1)
        fixture["mixed_population"] = {
            "session_id": "e2e_mixed_population",
            "import_id": mixed_import,
        }

        # 4. Captured payments awaiting settlement, no demo generated yet.
        pending_import = _snapshot(
            db, [_payment(0), _payment(1), _payment(2)], "pending"
        )
        mark_gateway_import_staged(db, pending_import)
        _stage_pending_payments(settings, pending_import, "e2e_pending", 3)
        fixture["pending"] = {"session_id": "e2e_pending", "import_id": pending_import}

        # 5. An empty session, for cross-session isolation checks.
        fixture["empty"] = {"session_id": "e2e_empty", "import_id": None}

        # Separate sessions for mutation tests; cold-restore fixtures remain immutable.
        fixture["manual_replace"] = {
            "session_id": "e2e_manual_replace",
            "import_id": demo_import,
            "evidence_id": _stage_demo_bundle(
                db, settings, demo_import, "e2e_manual_replace", payloads
            ),
        }
        invalid_import = _snapshot(db, [_payment(8, complete=False)], "missing_fields")
        _stage_pending_payments(settings, invalid_import, "e2e_missing_fields", 1)
        fixture["missing_fields"] = {
            "session_id": "e2e_missing_fields",
            "import_id": invalid_import,
        }

        # Two persisted runs, executed before Uvicorn starts so browser tests
        # never trigger work in the process Playwright later tears down.
        #
        # The CLEAN run is seeded FIRST so the dev run stays the active one and
        # existing scenarios are unaffected. Chunk 3C makes the clean run
        # addressable by id through the URL selection, which is how the
        # zero-exception scenario reaches it.
        clean = execute_run(
            inputs_dir=_write_clean_inputs(args.staging.parent),
            database=db,
            mode="rules-only",
            force=True,
        )
        exceptions = execute_run(
            inputs_dir=REPO_ROOT / "datasets" / "dev" / "inputs",
            database=db,
            mode="rules-only",
            force=True,
        )
        fixture["runs"] = {
            "clean_run_id": clean.run_id,
            "exception_run_id": exceptions.run_id,
        }
    finally:
        db.close()

    print(json.dumps(fixture, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
