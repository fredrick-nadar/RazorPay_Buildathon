"""Read-only transport retries, deadline and complete pagination; no network calls."""

import io
import json
import urllib.error
from datetime import UTC, date, datetime
from email.message import Message
from typing import Any

import pytest

from app.config import Settings
from app.importers import razorpay_client as transport
from app.importers.razorpay_client import RazorpayClient, RazorpayFetchResult
from app.main import create_app


def _clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    clock = [0.0]
    monkeypatch.setattr(transport.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        transport.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )
    return clock


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_transient_read_failure_retries_then_succeeds(
    code: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _clock(monkeypatch)
    calls = []

    def request(req: Any, timeout: float) -> io.BytesIO:
        calls.append(req)
        if len(calls) == 1:
            headers = Message()
            headers["Retry-After"] = "2"
            raise urllib.error.HTTPError(req.full_url, code, "redacted", headers, None)
        return io.BytesIO(b'{"items": [{"id": "pay_fictional"}]}')

    monkeypatch.setattr(transport.urllib.request, "urlopen", request)
    result = RazorpayClient("rzp_test_fixture", "fictional-secret").fetch_all_payments()
    assert result.success and len(result.items) == 1
    assert len(calls) == 2 and clock[0] == 2
    assert all(req.method == "GET" for req in calls)


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_permanent_failures_are_not_retried_or_echoed(
    code: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clock(monkeypatch)
    calls = []

    def request(req: Any, timeout: float) -> None:
        calls.append(req)
        raise urllib.error.HTTPError(req.full_url, code, "fictional-secret", Message(), None)

    monkeypatch.setattr(transport.urllib.request, "urlopen", request)
    result = RazorpayClient("rzp_test_fixture", "fictional-secret").fetch_payments()
    assert not result.success and len(calls) == 1
    assert result.error_code == f"HTTP_{code}"
    assert "fictional-secret" not in result.reason


def test_network_failures_are_bounded_and_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _clock(monkeypatch)
    calls = []

    def request(req: Any, timeout: float) -> None:
        calls.append(timeout)
        raise urllib.error.URLError("fictional-secret")

    monkeypatch.setattr(transport.urllib.request, "urlopen", request)
    result = RazorpayClient("rzp_test_fixture", "fictional-secret").fetch_payments()
    assert result.error_code == "NETWORK_ERROR"
    assert len(calls) == 3 and clock[0] == 1.5
    assert "fictional-secret" not in result.reason


def test_shared_deadline_covers_other_feeds_and_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _clock(monkeypatch)
    calls = []

    def request(req: Any, timeout: float) -> io.BytesIO:
        calls.append(timeout)
        clock[0] += 1
        if len(calls) == 1:
            return io.BytesIO(b'{"items": []}')
        headers = Message()
        headers["Retry-After"] = "60"
        raise urllib.error.HTTPError(req.full_url, 429, "redacted", headers, None)

    monkeypatch.setattr(transport.urllib.request, "urlopen", request)
    client = RazorpayClient("rzp_test_fixture", "fictional-secret", total_timeout_s=3)
    assert client.fetch_orders().success
    assert client.fetch_payments().error_code == "DEADLINE_EXCEEDED"
    assert calls == [3, 2]
    clock[0] = 3
    assert client.fetch_refunds().error_code == "DEADLINE_EXCEEDED"
    assert len(calls) == 2


@pytest.mark.parametrize("body", [b"{}", b"[]", b'{"items": null}', b'{"items": [1]}', b"not-json"])
def test_malformed_response_is_not_an_empty_success(
    body: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        transport.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(body)
    )
    result = RazorpayClient("rzp_test_fixture", "fictional-secret").fetch_payments()
    assert not result.success and result.error_code == "INVALID_RESPONSE"
    assert result.items == []


@pytest.mark.parametrize("count", [600, 601])
def test_exact_limit_is_verified_and_excess_never_silently_truncated(
    count: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def page(endpoint: str, params: dict[str, Any]) -> RazorpayFetchResult:
        skip, size = params["skip"], params["count"]
        calls.append((skip, size))
        return RazorpayFetchResult(
            True,
            False,
            "OK",
            [{"id": f"pay_fictional_{i}"} for i in range(skip, min(count, skip + size))],
        )

    client = RazorpayClient("rzp_test_fixture", "fictional-secret")
    monkeypatch.setattr(client, "_get", page)
    result = client.fetch_all_payments(max_records=600)
    assert calls[-1] == (600, 1)
    assert result.success is (count == 600)
    assert len(result.items) == (600 if count == 600 else 0)
    if count == 601:
        assert result.error_code == "RECORD_LIMIT_EXCEEDED"


def test_failed_later_page_returns_no_partial_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    client = RazorpayClient("rzp_test_fixture", "fictional-secret")

    def page(endpoint: str, params: dict[str, Any]) -> RazorpayFetchResult:
        if params["skip"] == 0:
            return RazorpayFetchResult(True, False, "OK", [{"id": str(i)} for i in range(100)])
        return RazorpayFetchResult(False, False, "Retry exhausted", error_code="HTTP_503")

    monkeypatch.setattr(client, "_get", page)
    result = client.fetch_all_payments()
    assert not result.success and result.items == [] and result.error_code == "HTTP_503"


def test_month_filter_precedes_record_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    client = RazorpayClient("rzp_test_fixture", "fictional-secret")
    before = int(datetime(2026, 3, 1, tzinfo=UTC).timestamp())
    inside = int(datetime(2026, 3, 5, tzinfo=UTC).timestamp())
    body = {
        "items": [{"settled_at": before, "entity_id": str(i)} for i in range(10)]
        + [{"settled_at": inside, "entity_id": "pay_selected"}]
    }
    monkeypatch.setattr(
        transport.urllib.request,
        "urlopen",
        lambda *args, **kwargs: io.BytesIO(json.dumps(body).encode()),
    )
    result = client.fetch_settlement_reconciliation(
        period_start=date(2026, 3, 5),
        period_end=date(2026, 3, 6),
        max_records=1,
    )
    assert result.success and [row["entity_id"] for row in result.items] == ["pay_selected"]


def test_sync_maps_shared_deadline_to_retryable_gateway_timeout(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    import app.api.routes_razorpay as routes

    class DeadlineClient:
        key_id = "rzp_test_fixture"
        is_configured = True

        def __init__(self, **kwargs: Any) -> None:
            self.calls = 0

        def fetch_all_orders(self, **kwargs: Any) -> RazorpayFetchResult:
            self.calls += 1
            return RazorpayFetchResult(
                False, False, "Import fetch deadline exceeded.", error_code="DEADLINE_EXCEEDED"
            )

        def fetch_all_payments(self, **kwargs: Any) -> RazorpayFetchResult:
            raise AssertionError("fetching must stop after the first failed resource")

        fetch_all_refunds = fetch_all_payments
        fetch_all_settlements = fetch_all_payments
        fetch_settlement_reconciliation = fetch_all_payments

    monkeypatch.setattr(routes, "RazorpayClient", DeadlineClient)
    settings = Settings(
        db_path=tmp_path / "fetch.sqlite3",
        import_staging_root=tmp_path / "imports",
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/razorpay/sync",
            json={
                "key_id": "rzp_test_fixture",
                "key_secret": "fictional-secret",
                "session_id": "timeout",
                "period_start": "2026-03-01",
                "period_end": "2026-03-02",
                "count": 1000,
            },
        )
    assert response.status_code == 504
    assert response.json()["detail"].endswith("No data was imported.")
