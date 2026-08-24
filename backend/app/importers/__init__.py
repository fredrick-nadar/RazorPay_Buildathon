"""Importers: typed source adapters and dataset normalization (PRD 8.1, Phase 6).

Runtime import code reads only dataset ``inputs`` directories. It never
imports the evaluator package and never reads ground-truth data.
"""

from app.importers.razorpay import (
    RazorpayAdapter,
    WebhookSignatureError,
    process_razorpay_webhook_event,
    verify_razorpay_webhook_signature,
)
from app.importers.razorpay_client import RazorpayClient, RazorpayFetchResult

__all__ = [
    "RazorpayAdapter",
    "RazorpayClient",
    "RazorpayFetchResult",
    "WebhookSignatureError",
    "process_razorpay_webhook_event",
    "verify_razorpay_webhook_signature",
]
