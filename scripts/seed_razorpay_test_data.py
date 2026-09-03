"""Seed a 500-order inventory into Razorpay Test Mode and verify retrieval.

Orders are payment intents, not captured payments. They are useful for testing
gateway discovery and pagination but are intentionally ineligible for financial
reconciliation until a genuine Test Mode Checkout payment is captured.
"""

from __future__ import annotations

import base64
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Add backend to python path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.importers.razorpay_client import RazorpayClient


def post_json_with_retry(
    url: str, auth_header: str, payload: dict, max_retries: int = 5
) -> dict | None:
    data = json.dumps(payload).encode("utf-8")
    delay = 0.2
    for attempt in range(max_retries):
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": auth_header,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:  # Rate limited
                time.sleep(delay)
                delay *= 1.5
                continue
            elif attempt < max_retries - 1:
                time.sleep(0.3)
                continue
            return None
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            if attempt < max_retries - 1:
                time.sleep(0.3)
                continue
            return None
    return None


def main() -> None:
    client = RazorpayClient()
    if not client.is_configured:
        print("[ERROR] Razorpay Test Mode credentials not configured!")
        sys.exit(1)

    print(f"[*] Authenticated as: {client.key_id}")
    auth_header = (
        "Basic "
        + base64.b64encode(f"{client.key_id}:{client.key_secret}".encode()).decode()
    )

    target_count = 500
    print(
        f"[*] Generating {target_count} order intents via "
        "POST https://api.razorpay.com/v1/orders ..."
    )

    created_ids: list[str] = []
    failed_count = 0
    start_time = time.time()

    def create_single_order(idx: int) -> str | None:
        amount_paise = (100 + (idx * 37) % 50000) * 100  # 100 to 50,000 INR in paise
        payload = {
            "amount": amount_paise,
            "currency": "INR",
            "receipt": f"argus_test_rec_{idx:04d}",
            "notes": {
                "batch": "argus_flight_recorder_demo",
                "index": str(idx),
                "source": "synthetic_live_seed",
            },
        }
        res = post_json_with_retry(
            "https://api.razorpay.com/v1/orders", auth_header, payload
        )
        if res:
            return res.get("id")
        return None

    # Use 8 workers with retry logic to stay well within Razorpay's API limits
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(create_single_order, i + 1) for i in range(target_count)
        ]
        for i, fut in enumerate(as_completed(futures), start=1):
            order_id = fut.result()
            if order_id:
                created_ids.append(order_id)
            else:
                failed_count += 1
            if i % 50 == 0 or i == target_count:
                elapsed = max(time.time() - start_time, 0.001)
                print(
                    f"    -> Progress: {i}/{target_count} processed ({len(created_ids)} succeeded, {failed_count} failed) in {elapsed:.1f}s ({len(created_ids) / elapsed:.1f} req/s)"
                )

    elapsed_total = time.time() - start_time
    print(
        f"\n[OK] Finished POST creation: {len(created_ids)} created, {failed_count} failed in {elapsed_total:.2f}s"
    )

    # -------------------------------------------------------------
    # Now query GET to verify how many records are returned!
    # -------------------------------------------------------------
    print("\n[*] Querying GET https://api.razorpay.com/v1/orders (with pagination)...")
    fetched_orders: list[dict] = []
    skip = 0
    page_size = 100

    while True:
        url = f"https://api.razorpay.com/v1/orders?count={page_size}&skip={skip}"
        req = urllib.request.Request(url, headers={"Authorization": auth_header})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                items = data.get("items", [])
                if not items:
                    break
                fetched_orders.extend(items)
                print(
                    f"    Page fetched (skip={skip:03d}): {len(items)} items | Cumulative: {len(fetched_orders)}"
                )
                if len(items) < page_size:
                    break
                skip += page_size
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
        ) as e:
            print(f"[!] Fetch error at skip={skip}: {e}")
            break

    print("\n=======================================================")
    print(" RESULTS SUMMARY FROM LIVE RAZORPAY TEST MODE API")
    print("=======================================================")
    print(
        " NOTE: Orders are imported as gateway source records, not as captured payments."
    )
    print(
        " Complete Razorpay Test Mode Checkout to create reconciliation-eligible payments."
    )
    print(f" Total POST Records Created in this run : {len(created_ids)}")
    print(f" Total GET Records Retrieved from API  : {len(fetched_orders)}")
    if fetched_orders:
        sample = fetched_orders[0]
        print(f" Sample Live Order ID                  : {sample.get('id')}")
        print(
            f" Sample Amount (Paise)                 : {sample.get('amount')} (INR {sample.get('amount', 0) / 100:.2f})"
        )
        print(f" Sample Receipt                        : {sample.get('receipt')}")
        print(f" Sample Status                         : {sample.get('status')}")
    print("=======================================================")


if __name__ == "__main__":
    main()
