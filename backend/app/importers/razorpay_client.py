"""Read-only Razorpay Test Mode Client (PRD Phase 6).

Safe, read-only adapter for merchant test-mode data. Never performs mutations,
never moves live funds, and gracefully skips when credentials are absent.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
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


class RazorpayClient:
    """Read-only client for documented Razorpay Test Mode endpoints."""

    BASE_URL = "https://api.razorpay.com/v1"

    def __init__(
        self,
        key_id: str | None = None,
        key_secret: str | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        settings = get_settings()
        self.key_id = key_id or getattr(settings, "razorpay_key_id", None)
        raw_secret = key_secret or getattr(settings, "razorpay_key_secret", None)
        if isinstance(raw_secret, SecretStr):
            self.key_secret: str | None = raw_secret.get_secret_value()
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
            query_str = "?" + "&".join(f"{k}={v}" for k, v in params.items() if v is not None)

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
        """Read-only fetch of captured payments from Test Mode."""
        return self._get("payments", {"count": count})

    def fetch_refunds(self, count: int = 10) -> RazorpayFetchResult:
        """Read-only fetch of processed refunds from Test Mode."""
        return self._get("refunds", {"count": count})

    def fetch_settlements(self, count: int = 10) -> RazorpayFetchResult:
        """Read-only fetch of settlements from Test Mode."""
        return self._get("settlements", {"count": count})

    def smoke_test(self) -> dict[str, Any]:
        """Diagnostic smoke test checking credentials and read access."""
        if not self.is_configured:
            return {
                "status": "SKIPPED",
                "reason": "Razorpay Test Mode credentials not configured (offline mode)",
                "read_access_verified": False,
            }

        res = self.fetch_payments(count=1)
        return {
            "status": "PASS" if res.success else "FAIL",
            "reason": res.reason,
            "read_access_verified": res.success,
            "items_returned": len(res.items),
        }
