"""Create a bounded, legitimate Razorpay Test Mode reconciliation dataset.

Payments cannot be inserted through the Payments API. This utility therefore
serves a localhost-only Standard Checkout page for existing Test Mode orders,
verifies every Checkout signature, captures authorised payments, and can then
create a small idempotent refund subset. Settlement and reconciliation records
remain Razorpay-owned and are only polled, never fabricated.

No API credential is written to disk or returned by the local HTTP server. The
state file contains only Razorpay entity IDs, integer paise amounts, statuses,
and timestamps and lives under the gitignored ``tmp/`` directory.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.importers.razorpay_client import RazorpayClient

DEFAULT_STATE_PATH = REPO_ROOT / "tmp" / "razorpay_test_lifecycle_state.json"
DEFAULT_REPORT_PATH = REPO_ROOT / "tmp" / "razorpay_test_lifecycle_verification.json"
DEFAULT_TARGET_PAYMENTS = 60
DEFAULT_TARGET_REFUNDS = 8
DEMO_CHECKOUT_CAP_PAISE = 500_000


class SeedError(RuntimeError):
    """Fail-closed lifecycle error safe to show in the local seed console."""


def _require_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SeedError(f"Razorpay returned a non-integer {field}")
    return value


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SeedError(f"Seed state does not exist: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("version") != 1:
        raise SeedError("Seed state has an unsupported format")
    return value


def _verify_checkout_signature(
    *, order_id: str, payment_id: str, signature: str, key_secret: str
) -> bool:
    expected = hmac.new(
        key_secret.encode("utf-8"),
        f"{order_id}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@dataclass
class TestModeApi:
    client: RazorpayClient

    def request(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not self.client.is_configured:
            raise SeedError("Razorpay Test Mode credentials are not configured")
        import base64

        auth = base64.b64encode(
            f"{self.client.key_id}:{self.client.key_secret}".encode()
        ).decode("ascii")
        headers = {
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
            "User-Agent": "ARGUS-Control-TestLifecycleSeeder/1.0",
            **(extra_headers or {}),
        }
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.client.BASE_URL}/{endpoint.lstrip('/')}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.reason
            try:
                response_body = json.loads(exc.read().decode("utf-8"))
                detail = response_body.get("error", {}).get("description", detail)
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
            raise SeedError(
                f"Razorpay {method} {endpoint} failed: HTTP {exc.code}: {detail}"
            ) from exc
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            raise SeedError(f"Razorpay {method} {endpoint} failed: {exc}") from exc
        if not isinstance(result, dict):
            raise SeedError(
                f"Razorpay {method} {endpoint} returned a non-object response"
            )
        return result


def prepare_state(api: TestModeApi, path: Path, target: int) -> dict[str, Any]:
    if target < 1 or target > 100:
        raise SeedError("target payments must be between 1 and 100")
    if path.is_file():
        state = _load_state(path)
        if state.get("target_payments") != target:
            raise SeedError(
                f"Existing state targets {state.get('target_payments')} payments, not {target}"
            )
        completed_ids = {str(item["order_id"]) for item in state["payments"]}
        retained = [
            order
            for order in state["orders"]
            if order["id"] in completed_ids
            or (
                isinstance(order.get("amount"), int)
                and not isinstance(order.get("amount"), bool)
                and order["amount"] <= DEMO_CHECKOUT_CAP_PAISE
            )
        ]
        if len(retained) == target:
            return state
        result = api.client.fetch_all_orders(max_records=1000)
        if not result.success:
            raise SeedError(result.reason)
        retained_ids = {str(order["id"]) for order in retained}
        replacements = [
            order
            for order in result.items
            if order.get("status") == "created"
            and isinstance(order.get("id"), str)
            and order["id"] not in retained_ids
            and isinstance(order.get("amount"), int)
            and not isinstance(order.get("amount"), bool)
            and order["amount"] <= DEMO_CHECKOUT_CAP_PAISE
            and isinstance(order.get("notes"), dict)
            and order["notes"].get("source") == "synthetic_live_seed"
        ]
        replacements.sort(
            key=lambda item: (int(item.get("created_at") or 0), str(item["id"]))
        )
        needed = target - len(retained)
        if len(replacements) < needed:
            raise SeedError(
                f"Need {needed} compatible unused ARGUS orders; Razorpay returned "
                f"{len(replacements)}"
            )
        state["orders"] = retained + [
            {
                "id": str(order["id"]),
                "amount": _require_int(order.get("amount"), "order amount"),
                "currency": str(order.get("currency") or ""),
                "receipt": str(order.get("receipt") or ""),
            }
            for order in replacements[:needed]
        ]
        _atomic_write_json(path, state)
        return state

    result = api.client.fetch_all_orders(max_records=1000)
    if not result.success:
        raise SeedError(result.reason)
    candidates = [
        order
        for order in result.items
        if order.get("status") == "created"
        and isinstance(order.get("id"), str)
        and isinstance(order.get("amount"), int)
        and not isinstance(order.get("amount"), bool)
        and order["amount"] <= DEMO_CHECKOUT_CAP_PAISE
        and isinstance(order.get("notes"), dict)
        and order["notes"].get("source") == "synthetic_live_seed"
    ]
    candidates.sort(
        key=lambda item: (int(item.get("created_at") or 0), str(item["id"]))
    )
    if len(candidates) < target:
        raise SeedError(
            f"Need {target} unused ARGUS orders; Razorpay returned {len(candidates)}"
        )
    orders = [
        {
            "id": str(order["id"]),
            "amount": _require_int(order.get("amount"), "order amount"),
            "currency": str(order.get("currency") or ""),
            "receipt": str(order.get("receipt") or ""),
        }
        for order in candidates[:target]
    ]
    state = {
        "version": 1,
        "mode": "RAZORPAY_TEST",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "target_payments": target,
        "orders": orders,
        "payments": [],
        "refunds": [],
    }
    _atomic_write_json(path, state)
    return state


class CheckoutCoordinator:
    def __init__(self, api: TestModeApi, state_path: Path, target: int) -> None:
        self.api = api
        self.state_path = state_path
        self.state = prepare_state(api, state_path, target)
        self.lock = threading.Lock()

    def public_status(self) -> dict[str, Any]:
        with self.lock:
            completed_ids = {str(item["order_id"]) for item in self.state["payments"]}
            next_order = next(
                (
                    order
                    for order in self.state["orders"]
                    if order["id"] not in completed_ids
                ),
                None,
            )
            return {
                "key_id": str(self.api.client.key_id),
                "target": int(self.state["target_payments"]),
                "completed": len(self.state["payments"]),
                "next_order": next_order,
            }

    def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        order_id = str(payload.get("razorpay_order_id") or "")
        payment_id = str(payload.get("razorpay_payment_id") or "")
        signature = str(payload.get("razorpay_signature") or "")
        if not order_id or not payment_id or not signature:
            raise SeedError("Checkout completion is missing signed identifiers")

        with self.lock:
            allowed_order = next(
                (order for order in self.state["orders"] if order["id"] == order_id),
                None,
            )
            if allowed_order is None:
                raise SeedError(
                    "Checkout returned an order outside this bounded seed batch"
                )
            existing = next(
                (
                    item
                    for item in self.state["payments"]
                    if item["order_id"] == order_id
                ),
                None,
            )
            if existing is not None:
                return {"accepted": True, "reused": True, "payment": existing}
            if not _verify_checkout_signature(
                order_id=order_id,
                payment_id=payment_id,
                signature=signature,
                key_secret=str(self.api.client.key_secret),
            ):
                raise SeedError("Checkout signature verification failed")

            payment = self.api.request("GET", f"payments/{payment_id}")
            if payment.get("order_id") != order_id:
                raise SeedError("Payment does not belong to the signed order")
            if (
                _require_int(payment.get("amount"), "payment amount")
                != allowed_order["amount"]
            ):
                raise SeedError(
                    "Payment amount differs from the immutable order amount"
                )
            if payment.get("status") == "authorized":
                payment = self.api.request(
                    "POST",
                    f"payments/{payment_id}/capture",
                    {
                        "amount": allowed_order["amount"],
                        "currency": allowed_order["currency"],
                    },
                )
            if payment.get("status") != "captured":
                raise SeedError(
                    f"Payment {payment_id} is {payment.get('status')}, not captured"
                )
            record = {
                "payment_id": payment_id,
                "order_id": order_id,
                "amount": allowed_order["amount"],
                "currency": allowed_order["currency"],
                "status": "captured",
                "created_at": _require_int(
                    payment.get("created_at"), "payment timestamp"
                ),
            }
            self.state["payments"].append(record)
            _atomic_write_json(self.state_path, self.state)
            return {"accepted": True, "reused": False, "payment": record}

    def reconcile_paid_orders(self) -> dict[str, Any]:
        """Recover captured payments when Checkout's browser callback is lost."""
        with self.lock:
            completed_ids = {
                str(item["order_id"]) for item in self.state["payments"]
            }
            recovered: list[dict[str, Any]] = []
            for order in self.state["orders"]:
                order_id = str(order["id"])
                if order_id in completed_ids:
                    continue
                remote_order = self.api.request("GET", f"orders/{order_id}")
                if remote_order.get("status") != "paid":
                    # Orders are processed sequentially by this bounded seeder.
                    # Once the first unpaid order is reached, later orders have
                    # not been presented to Checkout and need no remote lookup.
                    break
                response = self.api.request("GET", f"orders/{order_id}/payments")
                items = response.get("items")
                if not isinstance(items, list):
                    raise SeedError(
                        f"Razorpay returned invalid payments for order {order_id}"
                    )
                candidates = [
                    payment
                    for payment in items
                    if isinstance(payment, dict)
                    and payment.get("order_id") == order_id
                    and payment.get("status") == "captured"
                    and payment.get("amount") == order["amount"]
                ]
                if len(candidates) != 1:
                    raise SeedError(
                        f"Paid order {order_id} has {len(candidates)} matching captured payments"
                    )
                payment = candidates[0]
                payment_id = str(payment.get("id") or "")
                if not payment_id:
                    raise SeedError(f"Paid order {order_id} has a payment without an id")
                record = {
                    "payment_id": payment_id,
                    "order_id": order_id,
                    "amount": _require_int(payment.get("amount"), "payment amount"),
                    "currency": str(payment.get("currency") or ""),
                    "status": "captured",
                    "created_at": _require_int(
                        payment.get("created_at"), "payment timestamp"
                    ),
                }
                self.state["payments"].append(record)
                completed_ids.add(order_id)
                recovered.append(record)
            if recovered:
                _atomic_write_json(self.state_path, self.state)
            return {
                "recovered": len(recovered),
                "completed": len(self.state["payments"]),
            }


CHECKOUT_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ARGUS Test Mode Seeder</title><script src="https://checkout.razorpay.com/v1/checkout.js"></script>
<style>body{font-family:system-ui,sans-serif;max-width:680px;margin:48px auto;padding:0 20px;color:#0f172a}button{background:#0f172a;color:white;border:0;border-radius:10px;padding:12px 18px;font-weight:700}button:disabled{opacity:.4}.card{border:1px solid #cbd5e1;border-radius:16px;padding:24px}code{font-family:ui-monospace,monospace}.muted{color:#64748b;font-size:13px}</style></head>
<body><div class="card"><h1>ARGUS Razorpay Test Seeder</h1><p id="progress" class="muted">Loading bounded batch…</p><p id="order"></p><button id="pay" disabled>Create next Test Mode payment</button><p id="status" class="muted"></p></div>
<script>
const pay=document.getElementById('pay'), progress=document.getElementById('progress'), order=document.getElementById('order'), status=document.getElementById('status'); let snapshot=null;
async function refresh(){snapshot=await fetch('/api/status').then(r=>r.json());progress.textContent=`${snapshot.completed}/${snapshot.target} captured`;order.textContent=snapshot.next_order?`${snapshot.next_order.receipt} · INR ${(snapshot.next_order.amount/100).toFixed(2)}`:'Batch complete';pay.disabled=!snapshot.next_order;}
pay.onclick=()=>{status.textContent='Checkout open…';const current=snapshot.next_order,previous=snapshot.completed;const checkout=new Razorpay({key:snapshot.key_id,amount:current.amount,currency:current.currency,name:'ARGUS CONTROL',description:'Synthetic Test Mode reconciliation seed',order_id:current.id,prefill:{name:'ARGUS Demo Merchant',email:'argus.demo@example.com',contact:'+6591119111'},retry:{enabled:false},handler:async response=>{status.textContent='Verifying and capturing…';const result=await fetch('/api/complete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(response)}).then(async r=>({ok:r.ok,body:await r.json()}));if(!result.ok){status.textContent=result.body.error||'Capture failed';return;}status.textContent=`Captured ${result.body.payment.payment_id}`;await refresh();}});checkout.on('payment.failed',e=>{status.textContent=e.error.description||'Payment failed';});checkout.open();const recovery=setInterval(async()=>{try{const result=await fetch('/api/reconcile',{method:'POST'}).then(r=>r.json());if(result.completed>previous){clearInterval(recovery);status.textContent='Recovered captured payment from Razorpay';checkout.close();await refresh();}}catch{}},3000);setTimeout(()=>clearInterval(recovery),60000);};refresh();
</script></body></html>"""


def _handler_factory(coordinator: CheckoutCoordinator) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def _json(self, status: HTTPStatus, value: dict[str, Any]) -> None:
            body = json.dumps(value).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/":
                body = CHECKOUT_HTML.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/api/status":
                self._json(HTTPStatus.OK, coordinator.public_status())
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path == "/api/reconcile":
                try:
                    self._json(HTTPStatus.OK, coordinator.reconcile_paid_orders())
                except SeedError as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            if self.path != "/api/complete":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 2 or length > 16_384:
                    raise SeedError("Invalid completion payload length")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise SeedError("Completion payload must be an object")
                self._json(HTTPStatus.OK, coordinator.complete(payload))
            except (SeedError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    return Handler


def serve_checkout(api: TestModeApi, state_path: Path, target: int, port: int) -> None:
    coordinator = CheckoutCoordinator(api, state_path, target)
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler_factory(coordinator))
    print(f"ARGUS Test Mode Checkout ready: http://127.0.0.1:{port}")
    print(f"Progress: {len(coordinator.state['payments'])}/{target} captured")
    server.serve_forever()


def create_refunds(api: TestModeApi, state_path: Path, target: int) -> None:
    state = _load_state(state_path)
    if target < 0 or target > len(state["payments"]):
        raise SeedError("refund target exceeds captured payment count")
    refunded_payment_ids = {str(item["payment_id"]) for item in state["refunds"]}
    candidates = [
        item
        for item in state["payments"]
        if item["payment_id"] not in refunded_payment_ids
    ]
    needed = target - len(state["refunds"])
    for payment in candidates[: max(0, needed)]:
        amount = _require_int(payment["amount"], "stored payment amount")
        refund_amount = min(amount, max(100, amount // 10))
        payment_id = str(payment["payment_id"])
        refund = api.request(
            "POST",
            f"payments/{payment_id}/refund",
            {
                "amount": refund_amount,
                "receipt": f"argus-refund-{payment_id}",
                "notes": {"source": "argus_flight_recorder_demo"},
            },
            {"X-Refund-Idempotency": f"argus-seed-{payment_id}"},
        )
        state["refunds"].append(
            {
                "refund_id": str(refund.get("id") or ""),
                "payment_id": payment_id,
                "amount": _require_int(refund.get("amount"), "refund amount"),
                "status": str(refund.get("status") or ""),
                "created_at": _require_int(
                    refund.get("created_at"), "refund timestamp"
                ),
            }
        )
        _atomic_write_json(state_path, state)
        print(f"Refunded {len(state['refunds'])}/{target}: {payment_id}")


def verify_official_feeds(
    api: TestModeApi, state_path: Path, report_path: Path
) -> None:
    state = _load_state(state_path)
    timestamps = [int(item["created_at"]) for item in state["payments"]]
    period_start = (
        datetime.fromtimestamp(min(timestamps), UTC).date() - timedelta(days=1)
        if timestamps
        else datetime.now(UTC).date() - timedelta(days=1)
    )
    period_end = datetime.now(UTC).date() + timedelta(days=1)
    resources = {
        "orders": api.client.fetch_all_orders(max_records=1000),
        "payments": api.client.fetch_all_payments(max_records=1000),
        "refunds": api.client.fetch_all_refunds(max_records=1000),
        "settlements": api.client.fetch_all_settlements(max_records=1000),
        "reconciliation": api.client.fetch_settlement_reconciliation(
            period_start=period_start, period_end=period_end, max_records=1000
        ),
    }
    summary = {
        name: {
            "success": result.success,
            "count": len(result.items),
            "reason": result.reason,
        }
        for name, result in resources.items()
    }
    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "period_start": str(period_start),
        "period_end": str(period_end),
        "approved_batch": {
            "captured_payments": len(state["payments"]),
            "refunds": len(state["refunds"]),
        },
        "official_feeds": summary,
        "gateway_ready": (
            len(state["payments"]) == int(state["target_payments"])
            and len(state["refunds"]) == DEFAULT_TARGET_REFUNDS
        ),
        "settlement_ready": bool(resources["settlements"].items)
        and bool(resources["reconciliation"].items),
    }
    _atomic_write_json(report_path, report)
    print(json.dumps(report, indent=2))
    if not all(result.success for result in resources.values()):
        raise SeedError("One or more official Razorpay feed checks failed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve-checkout")
    serve.add_argument("--count", type=int, default=DEFAULT_TARGET_PAYMENTS)
    serve.add_argument("--port", type=int, default=8765)
    refunds = subparsers.add_parser("create-refunds")
    refunds.add_argument("--count", type=int, default=DEFAULT_TARGET_REFUNDS)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    client = RazorpayClient()
    api = TestModeApi(client)
    if not client.is_configured:
        raise SeedError("Razorpay Test Mode credentials are not configured")
    if args.command == "serve-checkout":
        serve_checkout(api, args.state, args.count, args.port)
    elif args.command == "create-refunds":
        create_refunds(api, args.state, args.count)
    elif args.command == "verify":
        verify_official_feeds(api, args.state, args.report)


if __name__ == "__main__":
    try:
        main()
    except SeedError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
