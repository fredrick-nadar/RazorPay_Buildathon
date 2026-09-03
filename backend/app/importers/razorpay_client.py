"""Read-only Razorpay Test Mode Client (PRD Phase 6).

Safe, read-only adapter for merchant test-mode data. Never performs mutations,
never moves live funds, and gracefully skips when credentials are absent.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from pydantic import SecretStr

from app.config import get_settings


@dataclass(frozen=True)
class RazorpayFetchResult:
    success: bool
    skipped: bool
    reason: str
    items: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "skipped": self.skipped,
            "reason": self.reason,
            "item_count": len(self.items),
        }


_UNSET: Any = object()


class RazorpayClient:
    """Read-only client for fetching records from Razorpay Test Mode API."""

    BASE_URL = "https://api.razorpay.com/v1"

    def __init__(
        self,
        key_id: str | None | Any = _UNSET,
        key_secret: str | None | Any = _UNSET,
        timeout_s: float = 10.0,
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

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                items = data.get("items", []) if isinstance(data, dict) else []
                return RazorpayFetchResult(
                    success=True,
                    skipped=False,
                    reason="OK",
                    items=items,
                )
        except urllib.error.HTTPError as exc:
            return RazorpayFetchResult(
                success=False,
                skipped=False,
                reason=f"HTTP {exc.code}: {exc.reason}",
                items=[],
            )
        except Exception as exc:
            return RazorpayFetchResult(
                success=False,
                skipped=False,
                reason=f"Network error: {str(exc)}",
                items=[],
            )

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
    ) -> RazorpayFetchResult:
        """Fetch a complete bounded snapshot using Razorpay's count/skip contract."""
        if max_records < 1:
            raise ValueError("max_records must be positive")
        all_items: list[dict[str, Any]] = []
        skip = 0
        while len(all_items) < max_records:
            page_size = min(page_limit, max_records - len(all_items))
            page_params = {**(params or {}), "count": page_size, "skip": skip}
            batch = self._get(endpoint, page_params)
            if not batch.success:
                return RazorpayFetchResult(
                    success=False,
                    skipped=batch.skipped,
                    reason=f"{endpoint} pagination failed at skip={skip}: {batch.reason}",
                    items=[],
                )
            if not batch.items:
                break
            all_items.extend(batch.items)
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
        while cursor <= period_end and len(collected) < max_records:
            remaining = max_records - len(collected)
            batch = self._fetch_all(
                "settlements/recon/combined",
                remaining,
                params={"year": cursor.year, "month": f"{cursor.month:02d}"},
                page_limit=1000,
            )
            if not batch.success:
                return RazorpayFetchResult(
                    success=False,
                    skipped=batch.skipped,
                    reason=f"settlement reconciliation failed for {cursor:%Y-%m}: {batch.reason}",
                    items=[],
                )
            for item in batch.items:
                settled_at = item.get("settled_at")
                if isinstance(settled_at, int) and not isinstance(settled_at, bool):
                    settled_date = datetime.fromtimestamp(settled_at, UTC).date()
                    if settled_date < period_start or settled_date > period_end:
                        continue
                collected.append(item)
                if len(collected) == max_records:
                    break
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
