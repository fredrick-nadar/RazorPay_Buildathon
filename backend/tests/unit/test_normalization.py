"""Phase 2 normalization tests: adapters, quarantine, dedup, reorder safety.

Every physical row must remain accounted for (accepted + quarantined +
duplicate delivery == raw rows), quarantined rows keep full provenance, and
duplicate-id resolution is order-independent: identical hashes accept one
economic record, differing hashes quarantine every conflicting row.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.domain.enums import QuarantineReason
from app.importers.adapters import ADAPTER_SPECS
from app.importers.ingest import IngestError, ingest_inputs

_HEADERS = {spec.file_stem: spec.columns for spec in ADAPTER_SPECS}

_PAYMENT_ROW = {
    "payment_id": "pay_AAA0000001",
    "order_id": "order_AAA0000001",
    "status": "CAPTURED",
    "currency": "INR",
    "gross_amount": "100.00",
    "fee_amount": "2.00",
    "tax_amount": "0.36",
    "captured_at_utc": "2026-03-02T03:17:28Z",
    "settlement_id": "stl_AAA0000001",
}
_SETTLEMENT_ROW = {
    "settlement_id": "stl_AAA0000001",
    "settled_at_utc": "2026-03-03T04:18:47Z",
    "window_start_utc": "2026-03-02T00:00:00Z",
    "window_end_utc": "2026-03-03T00:00:00Z",
    "status": "PROCESSED",
    "currency": "INR",
    "gross_credit": "100.00",
    "fee_amount": "2.00",
    "tax_amount": "0.36",
    "adjustment_amount": "0.00",
    "net_amount": "97.64",
    "utr": "UTIR540611714482",
}
_BANK_ROW = {
    "bank_entry_id": "bnk_AAA0000001",
    "posted_at_utc": "2026-03-03T04:23:47Z",
    "value_date": "2026-03-03",
    "currency": "INR",
    "signed_amount": "97.64",
    "narration": "NEFT CR UTIR540611714482 ARGUS DEMO MERCH",
    "utr": "UTIR540611714482",
    "account_fingerprint": "FP-ARGUS-DEMO-01",
}


def write_inputs(
    tmp_path: Path,
    payments: list[dict[str, str]] | None = None,
    refunds: list[dict[str, str]] | None = None,
    settlements: list[dict[str, str]] | None = None,
    bank_entries: list[dict[str, str]] | None = None,
    ledger_entries: list[dict[str, str]] | None = None,
) -> Path:
    inputs = tmp_path / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    payload = {
        "payments": payments if payments is not None else [dict(_PAYMENT_ROW)],
        "refunds": refunds if refunds is not None else [],
        "settlements": (settlements if settlements is not None else [dict(_SETTLEMENT_ROW)]),
        "bank_entries": bank_entries if bank_entries is not None else [dict(_BANK_ROW)],
        "ledger_entries": ledger_entries if ledger_entries is not None else [],
    }
    for stem, rows in payload.items():
        with (inputs / f"{stem}.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(_HEADERS[stem]), lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow({column: row.get(column, "") for column in _HEADERS[stem]})
    return inputs


def _refund_row(**overrides: str) -> dict[str, str]:
    row = {
        "refund_id": "rfd_AAA0000001",
        "payment_id": "pay_AAA0000001",
        "status": "PROCESSED",
        "currency": "INR",
        "refund_amount": "20.00",
        "created_at_utc": "2026-03-02T13:29:05Z",
        "settlement_id": "stl_AAA0000001",
    }
    row.update(overrides)
    return row


class TestHappyPathNormalization:
    def test_payment_parses_exact_paise_and_timestamp(self, tmp_path: Path) -> None:
        result = ingest_inputs(write_inputs(tmp_path))
        payment = result.records.payments[0]
        assert int(payment.gross_amount_paise) == 10_000
        assert int(payment.fee_paise) == 200
        assert int(payment.tax_paise) == 36
        assert int(payment.net_paise) == 9_764
        assert payment.captured_at_utc.year == 2026
        assert payment.provenance.source_row_number == 1
        assert payment.provenance.source_file == "inputs/payments.csv"
        assert len(payment.provenance.content_hash) == 64

    def test_row_accounting_identity_holds(self, tmp_path: Path) -> None:
        result = ingest_inputs(write_inputs(tmp_path))
        assert (
            result.accepted_count + result.quarantined_count + result.duplicate_delivery_count
            == result.raw_row_count
        )
        for stat in result.file_stats:
            assert stat.accepted + stat.quarantined + stat.duplicate_delivery == stat.raw_rows


class TestQuarantine:
    def test_usd_row_quarantined_with_provenance(self, tmp_path: Path) -> None:
        usd = dict(_PAYMENT_ROW)
        usd["currency"] = "USD"
        result = ingest_inputs(write_inputs(tmp_path, payments=[usd]))
        assert result.accepted_count == 2  # settlement + bank only
        quarantined = [row for row in result.rows if row.state == "QUARANTINED"]
        assert len(quarantined) == 1
        row = quarantined[0]
        assert row.quarantine_reason == QuarantineReason.UNSUPPORTED_CURRENCY
        assert row.source_record_id == "pay_AAA0000001"
        assert row.source_row_number == 1
        assert row.content_hash  # traceable, never dropped
        assert "currency" in row.raw_payload_json

    def test_invalid_timestamp_quarantined(self, tmp_path: Path) -> None:
        bad = _refund_row(created_at_utc="2026-13-45T99:00:00Z", settlement_id="")
        result = ingest_inputs(write_inputs(tmp_path, refunds=[bad]))
        reasons = [row.quarantine_reason for row in result.rows if row.state == "QUARANTINED"]
        assert reasons == [QuarantineReason.INVALID_TIMESTAMP]

    def test_invalid_money_quarantined(self, tmp_path: Path) -> None:
        bad = _refund_row(refund_amount="12.3.4")
        result = ingest_inputs(write_inputs(tmp_path, refunds=[bad]))
        reasons = [row.quarantine_reason for row in result.rows if row.state == "QUARANTINED"]
        assert reasons == [QuarantineReason.INVALID_MONEY]

    def test_unknown_status_quarantined(self, tmp_path: Path) -> None:
        bad = _refund_row(status="PENDING")
        result = ingest_inputs(write_inputs(tmp_path, refunds=[bad]))
        reasons = [row.quarantine_reason for row in result.rows if row.state == "QUARANTINED"]
        assert reasons == [QuarantineReason.UNKNOWN_STATUS]

    def test_missing_required_id_quarantined(self, tmp_path: Path) -> None:
        bad = _refund_row(refund_id="")
        result = ingest_inputs(write_inputs(tmp_path, refunds=[bad]))
        reasons = [row.quarantine_reason for row in result.rows if row.state == "QUARANTINED"]
        assert reasons == [QuarantineReason.MISSING_REQUIRED_FIELD]

    def test_row_with_extra_column_quarantined(self, tmp_path: Path) -> None:
        inputs = write_inputs(tmp_path)
        path = inputs / "refunds.csv"
        lines = path.read_text(encoding="utf-8").splitlines()
        bad = _refund_row(refund_id="rfd_EXTRA00001")
        values = [bad[column] for column in _HEADERS["refunds"]] + ["surplus"]
        path.write_text("\n".join([lines[0], ",".join(values)]) + "\n", encoding="utf-8")
        result = ingest_inputs(inputs)
        reasons = [row.quarantine_reason for row in result.rows if row.state == "QUARANTINED"]
        assert reasons == [QuarantineReason.INVALID_ROW_SHAPE]


class TestDuplicateDelivery:
    def test_identical_rows_deduplicate_to_one_event(self, tmp_path: Path) -> None:
        rows = [dict(_PAYMENT_ROW), dict(_PAYMENT_ROW)]
        result = ingest_inputs(write_inputs(tmp_path, payments=rows))
        assert len(result.records.payments) == 1
        duplicates = [row for row in result.rows if row.state == "DUPLICATE_DELIVERY"]
        assert len(duplicates) == 1
        assert duplicates[0].duplicate_of_row_number == 1
        assert duplicates[0].source_record_id == "pay_AAA0000001"

    def test_duplicate_resolution_is_reorder_independent(self, tmp_path: Path) -> None:
        first = write_inputs(tmp_path, payments=[dict(_PAYMENT_ROW), dict(_PAYMENT_ROW)])
        second = write_inputs(
            tmp_path / "shuffled",
            payments=[dict(_PAYMENT_ROW), dict(_PAYMENT_ROW)],
        )
        # Reverse row order in the second copy.
        path = second / "payments.csv"
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join([lines[0], lines[2], lines[1]]) + "\n", encoding="utf-8")
        one = ingest_inputs(first)
        two = ingest_inputs(second)
        assert one.accepted_count == two.accepted_count
        assert one.duplicate_delivery_count == two.duplicate_delivery_count
        assert [p.provenance.content_hash for p in one.records.payments] == [
            p.provenance.content_hash for p in two.records.payments
        ]
        for outcome_one, outcome_two in zip(one.rows, two.rows, strict=True):
            assert outcome_one.source_record_id == outcome_two.source_record_id
            assert outcome_one.state == outcome_two.state
            assert outcome_one.content_hash == outcome_two.content_hash


class TestDuplicateIdConflict:
    def test_conflicting_content_quarantines_every_row(self, tmp_path: Path) -> None:
        variant = dict(_PAYMENT_ROW)
        variant["gross_amount"] = "999.00"
        result = ingest_inputs(write_inputs(tmp_path, payments=[dict(_PAYMENT_ROW), variant]))
        assert result.records.payments == ()  # none accepted, never first-wins
        quarantined = [row for row in result.rows if row.state == "QUARANTINED"]
        assert len(quarantined) == 2
        assert {row.quarantine_reason for row in quarantined} == {
            QuarantineReason.DUPLICATE_ID_CONFLICT
        }

    def test_conflict_outcome_is_reorder_independent(self, tmp_path: Path) -> None:
        variant = dict(_PAYMENT_ROW)
        variant["gross_amount"] = "999.00"
        one = ingest_inputs(write_inputs(tmp_path / "a", payments=[dict(_PAYMENT_ROW), variant]))
        two = ingest_inputs(write_inputs(tmp_path / "b", payments=[variant, dict(_PAYMENT_ROW)]))
        assert one.accepted_count == two.accepted_count
        assert one.quarantined_count == two.quarantined_count
        assert {row.content_hash for row in one.rows} == {row.content_hash for row in two.rows}


class TestFileLevelFailures:
    def test_missing_file_rejected(self, tmp_path: Path) -> None:
        inputs = write_inputs(tmp_path)
        (inputs / "settlements.csv").unlink()
        with pytest.raises(IngestError):
            ingest_inputs(inputs)

    def test_extra_csv_rejected(self, tmp_path: Path) -> None:
        inputs = write_inputs(tmp_path)
        (inputs / "extra.csv").write_text("x\n1\n", encoding="utf-8")
        with pytest.raises(IngestError):
            ingest_inputs(inputs)

    def test_wrong_header_rejected(self, tmp_path: Path) -> None:
        inputs = write_inputs(tmp_path)
        path = inputs / "payments.csv"
        lines = path.read_text(encoding="utf-8").splitlines()
        header = lines[0].split(",")
        header[1] = "renamed_column"
        lines[0] = ",".join(header)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with pytest.raises(IngestError):
            ingest_inputs(inputs)

    def test_missing_directory_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(IngestError):
            ingest_inputs(tmp_path / "nowhere")
