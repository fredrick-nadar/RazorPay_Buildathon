"""Read-only Razorpay Test Mode Client (PRD Phase 6).

Safe, read-only adapter for merchant test-mode data. Never performs mutations,
never moves live funds, and gracefully skips when credentials are absent.
"""

from __future__ import annotations

import base64
import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from pydantic import SecretStr

from app.config import get_settings


@dataclass(frozen=True)
class RazorpayFetchResult:
    success: bool
    skipped: bool
    reason: str
    items: list[dict[str, Any]] = field(default_factory=list)
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "skipped": self.skipped,
            "reason": self.reason,
            "item_count": len(self.items),
            "error_code": self.error_code,
        }


_UNSET: Any = object()
IMPORT_FETCH_BUDGET_SECONDS = 90.0
MAX_PAGE_BYTES = 8 * 1024 * 1024


def _failure(code: str, reason: str) -> RazorpayFetchResult:
    return RazorpayFetchResult(False, False, reason, error_code=code)


def _retry_after(value: str | None, fallback: float) -> float:
    if value is None:
        return fallback
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return fallback


class RazorpayClient:
    """Read-only client for fetching records from Razorpay Test Mode API."""

    BASE_URL = "https://api.razorpay.com/v1"

    def __init__(
        self,
        key_id: str | None | Any = _UNSET,
        key_secret: str | None | Any = _UNSET,
        timeout_s: float = 10.0,
        total_timeout_s: float = IMPORT_FETCH_BUDGET_SECONDS,
    ) -> None:
        if key_id is not _UNSET or key_secret is not _UNSET:
            self.key_id = None if key_id is _UNSET else key_id
            self.key_secret = None if key_secret is _UNSET else key_secret
        else:
            settings = get_settings()
            self.key_id = getattr(settings, "razorpay_key_id", None)
            raw_secret = getattr(settings, "razorpay_key_secret", None)
            if isinstance(raw_secret, SecretStr):
                self.key_secret = raw_secret.get_secret_value()
            elif raw_secret:
                self.key_secret = str(raw_secret)
            else:
                self.key_secret = None

        self.timeout_s = timeout_s
        if timeout_s <= 0 or total_timeout_s <= 0:
            raise ValueError("Timeouts must be positive")
        self._deadline = time.monotonic() + total_timeout_s

    @property
    def is_configured(self) -> bool:
        """True only when valid Test Mode credentials are provided."""
        return bool(self.key_id and self.key_secret)

    def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> RazorpayFetchResult:
        if not self.is_configured:
            return RazorpayFetchResult(
                success=False,
                skipped=True,
                reason="Razorpay credentials not configured (offline synthetic mode active)",
                items=[],
            )

        query_str = ""
        if params:
            query_str = "?" + urllib.parse.urlencode(
                {key: value for key, value in params.items() if value is not None}
            )

        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}{query_str}"
        auth_header = base64.b64encode(f"{self.key_id}:{self.key_secret}".encode()).decode("ascii")

        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Basic {auth_header}",
                "User-Agent": "ARGUS-Control-TestClient/1.0",
                "Accept": "application/json",
            },
            method="GET",
        )

        for attempt in range(3):
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                return _failure(
                    "DEADLINE_EXCEEDED", "Import fetch deadline exceeded; try a shorter date range."
                )
            delay = 0.5 * (2**attempt)
            try:
                with urllib.request.urlopen(req, timeout=min(self.timeout_s, remaining)) as resp:
                    chunks = bytearray()
                    while True:
                        if time.monotonic() >= self._deadline:
                            return _failure("DEADLINE_EXCEEDED", "Import fetch deadline exceeded.")
                        chunk = resp.read1(65536)
                        if not chunk:
                            break
                        chunks.extend(chunk)
                        if len(chunks) > MAX_PAGE_BYTES:
                            return _failure(
                                "INVALID_RESPONSE", "Razorpay response exceeded the safe page size."
                            )
                    data = json.loads(chunks.decode("utf-8"))
                items = data.get("items") if isinstance(data, dict) else None
                if not isinstance(items, list) or any(not isinstance(row, dict) for row in items):
                    return _failure(
                        "INVALID_RESPONSE",
                        "Razorpay returned an invalid collection, not an empty feed.",
                    )
                return RazorpayFetchResult(True, False, "OK", items)
            except urllib.error.HTTPError as exc:
                code = exc.code
                retry_header = exc.headers.get("Retry-After") if exc.headers else None
                delay = _retry_after(retry_header, delay)
                exc.close()
                failure = _failure(f"HTTP_{code}", f"Razorpay returned HTTP {code}.")
                if code not in {429, 500, 502, 503, 504}:
                    return failure
            except (OSError, http.client.HTTPException):
                failure = _failure(
                    "NETWORK_ERROR", "Razorpay could not be reached within the request timeout."
                )
            except (ValueError, UnicodeError):
                return _failure("INVALID_RESPONSE", "Razorpay returned invalid JSON.")
            if attempt == 2:
                return failure
            if delay >= self._deadline - time.monotonic():
                return _failure(
                    "DEADLINE_EXCEEDED", "Retry would exceed the import deadline; try again later."
                )
            time.sleep(delay)
        raise AssertionError("bounded retry loop must return")

    def fetch_payments(self, count: int = 10) -> RazorpayFetchResult:
        """Read-only fetch of one payments page from Test Mode."""
        return self._get("payments", {"count": count})

    def fetch_orders(self, count: int = 100, skip: int = 0) -> RazorpayFetchResult:
        """Read-only fetch of created orders from Test Mode."""
        return self._get("orders", {"count": count, "skip": skip})

    def _fetch_all(
        self,
        endpoint: str,
        max_records: int,
        *,
        params: dict[str, Any] | None = None,
        page_limit: int = 100,
        item_filter: Callable[[dict[str, Any]], bool] | None = None,
    ) -> RazorpayFetchResult:
        """Fetch a complete bounded snapshot using Razorpay's count/skip contract."""
        if max_records < 1:
            raise ValueError("max_records must be positive")
        all_items: list[dict[str, Any]] = []
        skip = 0
        while True:
            if time.monotonic() >= self._deadline:
                return _failure(
                    "DEADLINE_EXCEEDED", "Import fetch deadline exceeded; narrow the date range."
                )
            # One extra row proves whether a full final page really was the end.
            page_size = (
                page_limit if item_filter else min(page_limit, max(1, max_records - len(all_items)))
            )
            page_params = {**(params or {}), "count": page_size, "skip": skip}
            batch = self._get(endpoint, page_params)
            if not batch.success:
                return RazorpayFetchResult(
                    success=False,
                    skipped=batch.skipped,
                    reason=f"{endpoint} pagination failed at skip={skip}: {batch.reason}",
                    items=[],
                    error_code=batch.error_code,
                )
            if not batch.items:
                break
            if len(batch.items) > page_size:
                return _failure(
                    "INVALID_RESPONSE", "Razorpay returned more rows than the requested page size."
                )
            all_items.extend(row for row in batch.items if item_filter is None or item_filter(row))
            if len(all_items) > max_records:
                return _failure(
                    "RECORD_LIMIT_EXCEEDED",
                    "The period exceeds the import record limit; narrow the date range.",
                )
            if len(batch.items) < page_size:
                break
            skip += page_size
        return RazorpayFetchResult(
            success=True,
            skipped=False,
            reason=f"Fetched {len(all_items)} {endpoint} across pagination",
            items=all_items,
        )

    def fetch_all_orders(
        self, max_records: int = 1000, *, from_ts: int | None = None, to_ts: int | None = None
    ) -> RazorpayFetchResult:
        return self._fetch_all("orders", max_records, params={"from": from_ts, "to": to_ts})

    def fetch_all_payments(
        self, max_records: int = 1000, *, from_ts: int | None = None, to_ts: int | None = None
    ) -> RazorpayFetchResult:
        return self._fetch_all("payments", max_records, params={"from": from_ts, "to": to_ts})

    def fetch_refunds(self, count: int = 10) -> RazorpayFetchResult:
        """Read-only fetch of one refunds page from Test Mode."""
        return self._get("refunds", {"count": count})

    def fetch_all_refunds(
        self, max_records: int = 1000, *, from_ts: int | None = None, to_ts: int | None = None
    ) -> RazorpayFetchResult:
        return self._fetch_all("refunds", max_records, params={"from": from_ts, "to": to_ts})

    def fetch_settlements(self, count: int = 10) -> RazorpayFetchResult:
        """Read-only fetch of one settlements page from Test Mode."""
        return self._get("settlements", {"count": count})

    def fetch_all_settlements(
        self, max_records: int = 1000, *, from_ts: int | None = None, to_ts: int | None = None
    ) -> RazorpayFetchResult:
        return self._fetch_all("settlements", max_records, params={"from": from_ts, "to": to_ts})

    def fetch_settlement_reconciliation(
        self,
        *,
        period_start: date,
        period_end: date,
        max_records: int = 1000,
    ) -> RazorpayFetchResult:
        """Fetch official combined settlement-reconciliation rows for a date range."""
        if period_end < period_start:
            raise ValueError("period_end cannot be before period_start")
        if max_records < 1:
            raise ValueError("max_records must be positive")

        collected: list[dict[str, Any]] = []
        cursor = date(period_start.year, period_start.month, 1)

        def in_period(item: dict[str, Any]) -> bool:
            settled_at = item.get("settled_at")
            if isinstance(settled_at, int) and not isinstance(settled_at, bool):
                try:
                    return (
                        period_start <= datetime.fromtimestamp(settled_at, UTC).date() <= period_end
                    )
                except (OverflowError, OSError, ValueError):
                    pass  # retain malformed timestamps for classification/quarantine
            return True

        while cursor <= period_end:
            remaining = max_records - len(collected)
            batch = self._fetch_all(
                "settlements/recon/combined",
                max(1, remaining),
                params={"year": cursor.year, "month": f"{cursor.month:02d}"},
                page_limit=1000,
                item_filter=in_period,
            )
            if not batch.success:
                return RazorpayFetchResult(
                    success=False,
                    skipped=batch.skipped,
                    reason=f"settlement reconciliation failed for {cursor:%Y-%m}: {batch.reason}",
                    items=[],
                    error_code=batch.error_code,
                )
            collected.extend(batch.items)
            if len(collected) > max_records:
                return _failure(
                    "RECORD_LIMIT_EXCEEDED",
                    "Settlement rows exceed the import limit; narrow the date range.",
                )
            cursor = (
                date(cursor.year + 1, 1, 1)
                if cursor.month == 12
                else date(cursor.year, cursor.month + 1, 1)
            )
        return RazorpayFetchResult(
            success=True,
            skipped=False,
            reason=f"Fetched {len(collected)} settlement reconciliation rows",
            items=collected,
        )

    def smoke_test(self) -> dict[str, Any]:
        """Diagnostic smoke test checking credentials and read access."""
        if not self.is_configured:
            return {
                "status": "SKIPPED",
                "reason": "Razorpay Test Mode credentials not configured (offline mode)",
                "read_access_verified": False,
            }

        payments = self.fetch_payments(count=1)
        orders = self.fetch_orders(count=1)
        success = payments.success and orders.success
        return {
            "status": "PASS" if success else "FAIL",
            "reason": "payment and order read access verified"
            if success
            else (payments.reason if not payments.success else orders.reason),
            "read_access_verified": success,
            "payments_returned": len(payments.items),
            "orders_returned": len(orders.items),
        }
