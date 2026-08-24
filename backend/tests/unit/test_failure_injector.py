"""Unit tests for EventFailureInjector (PRD Phase 6)."""

from __future__ import annotations

from app.failure_lab.injector import EventFailureInjector


def test_seed_reproducibility() -> None:
    injector1 = EventFailureInjector(seed=12345)
    injector2 = EventFailureInjector(seed=12345)

    sample_rows = [
        {"payment_id": f"pay_{i:03d}", "gross_amount": "100.00", "currency": "INR"}
        for i in range(20)
    ]

    res1, injs1 = injector1.inject_duplicate_rows(
        sample_rows, "payments.csv", "payment_id", rate=0.2
    )
    res2, injs2 = injector2.inject_duplicate_rows(
        sample_rows, "payments.csv", "payment_id", rate=0.2
    )

    assert res1 == res2
    assert len(injs1) == len(injs2)
    assert len(res1) > len(sample_rows)


def test_duplicate_injection_preserves_content() -> None:
    injector = EventFailureInjector(seed=42)
    sample_rows = [
        {"refund_id": f"rfnd_{i:03d}", "refund_amount": "50.00", "currency": "INR"}
        for i in range(10)
    ]
    res, injs = injector.inject_duplicate_rows(sample_rows, "refunds.csv", "refund_id", rate=0.3)

    assert len(res) == len(sample_rows) + len(injs)
    # Check that duplicated target_ids actually appear multiple times in result
    for inj in injs:
        matching = [r for r in res if r["refund_id"] == inj.target_id]
        assert len(matching) >= 2


def test_out_of_order_injection() -> None:
    injector = EventFailureInjector(seed=42)
    sample_rows = [{"settlement_id": f"setl_{i:03d}", "net_amount": "1000.00"} for i in range(10)]
    res, injs = injector.inject_out_of_order_rows(
        sample_rows, "settlements.csv", "settlement_id", distance=3
    )

    assert len(res) == len(sample_rows)
    assert len(injs) > 0
    # The order must have changed
    assert res != sample_rows


def test_missing_rows_injection() -> None:
    injector = EventFailureInjector(seed=99)
    sample_rows = [{"bank_entry_id": f"bnk_{i:03d}", "signed_amount": "500.00"} for i in range(20)]
    res, injs = injector.inject_missing_rows(
        sample_rows, "bank_entries.csv", "bank_entry_id", rate=0.1
    )

    assert len(res) == len(sample_rows) - len(injs)
    for inj in injs:
        assert not any(r["bank_entry_id"] == inj.target_id for r in res)


def test_corrupted_rows_injection() -> None:
    injector = EventFailureInjector(seed=77)
    sample_rows = [
        {
            "payment_id": f"pay_{i:03d}",
            "gross_amount": "250.00",
            "currency": "INR",
            "captured_at_utc": "2026-08-01T10:00:00Z",
        }
        for i in range(15)
    ]
    res, injs = injector.inject_corrupted_rows(sample_rows, "payments.csv", "payment_id", rate=0.2)

    assert len(res) == len(sample_rows)
    assert len(injs) > 0
    # Injected rows should have corrupted currency or amount or date
    for inj in injs:
        target = next(r for r in res if r["payment_id"] == inj.target_id)
        assert (
            target["currency"] == "USD"
            or target["gross_amount"] == "INVALID_AMT"
            or target["captured_at_utc"] == "NOT_A_DATE"
        )
