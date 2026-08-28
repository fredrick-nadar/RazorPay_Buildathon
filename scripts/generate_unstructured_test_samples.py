"""Generate 3 unstructured test documents with non-standard, real-world messy headers for AI schema reconstruction testing."""

from __future__ import annotations

import csv
import decimal
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
test_dir = root_dir / "test_unstructured_samples"
test_dir.mkdir(parents=True, exist_ok=True)

RANDOM_SEED = 99
random.seed(RANDOM_SEED)

START_DATE = datetime(2026, 3, 5, 10, 0, 0, tzinfo=timezone.utc)


def generate_unstructured_test_suite() -> None:
    print("[ARGUS] Generating 3 unstructured real-world test documents with arbitrary headers...")

    num_txns = 50
    num_payouts = 5  # 10 transactions per payout batch

    txns: list[dict[str, str]] = []
    payouts: list[dict[str, str]] = []
    journals: list[dict[str, str]] = []

    amount_tiers = [999.00, 1499.00, 2499.00, 4999.00, 7500.00, 12500.00]
    payout_buckets: dict[int, list[dict[str, str]]] = {i: [] for i in range(1, num_payouts + 1)}

    for i in range(1, num_txns + 1):
        txn_ref = f"txn_MESSY_{i:04d}"
        cust_order = f"ORD_CART_{i:04d}"
        batch_idx = min((i - 1) // 10 + 1, num_payouts)
        payout_batch_ref = f"BATCH_DISBURSE_{batch_idx:02d}"

        txn_time = START_DATE + timedelta(minutes=i * 45)
        time_str = txn_time.strftime("%Y-%m-%d %H:%M:%S")

        billed_total = decimal.Decimal(str(random.choice(amount_tiers)))
        charge = (billed_total * decimal.Decimal("0.02")).quantize(decimal.Decimal("0.01"))
        tax = (charge * decimal.Decimal("0.18")).quantize(decimal.Decimal("0.01"))
        net_val = billed_total - charge - tax

        # 1. Unstructured Gateway CSV with arbitrary, messy headers
        t_row = {
            "Transaction_Ref": txn_ref,
            "Cust_Order_No": cust_order,
            "Txn_Status": "PAID_CAPTURED",
            "Currency_ISO": "INR",
            "Billed_Total_Amount": f"{billed_total:.2f}",
            "Gateway_MDR_Charge": f"{charge:.2f}",
            "Govt_Tax_GST": f"{tax:.2f}",
            "Created_Timestamp": time_str,
            "Batch_Payout_Ref": payout_batch_ref,
        }
        txns.append(t_row)
        payout_buckets[batch_idx].append(t_row)

        # 2. Unstructured ERP Journal CSV with Tally/Zoho-style custom headers
        journals.append({
            "Voucher_No": f"VCH_JRNL_{i:04d}",
            "Ledger_Head": "2100-PAYMENTS-CLEARING",
            "Booking_Date": time_str[:10],
            "Currency_Symbol": "INR",
            "Net_Journal_Value": f"{net_val:.2f}",
            "Source_Doc_Number": txn_ref,
            "Doc_Type": "PAYMENT",
            "Audit_Remarks": f"Online customer checkout {cust_order}",
            "Entry_Source": "AUTOMATED_IMPORT",
        })

    # 3. Unstructured Bank Statement CSV with ICICI Bank Corporate Narration headers
    for b_idx in range(1, num_payouts + 1):
        bucket = payout_buckets[b_idx]
        batch_ref = f"BATCH_DISBURSE_{b_idx:02d}"
        utr_num = f"ICICR2603819_{b_idx:03d}"

        gross_tot = sum(decimal.Decimal(r["Billed_Total_Amount"]) for r in bucket)
        fee_tot = sum(decimal.Decimal(r["Gateway_MDR_Charge"]) for r in bucket)
        tax_tot = sum(decimal.Decimal(r["Govt_Tax_GST"]) for r in bucket)
        net_payout = gross_tot - fee_tot - tax_tot

        payout_time = START_DATE + timedelta(days=b_idx)
        payouts.append({
            "Posting_Date": payout_time.strftime("%d/%m/%Y"),
            "Chq_Ref_No": utr_num,
            "Txn_Remarks": f"INF/NEFT/0029312019/PAYMENT AGGREGATOR SETTLEMENT/{batch_ref}",
            "Currency": "INR",
            "Credit_Amount": f"{net_payout:.2f}",
            "Bank_Account_Number": "ICICI_CORP_00091244",
        })

    # Save 1. Unstructured Gateway Export
    gw_path = test_dir / "unstructured_gateway_report.csv"
    with open(gw_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(txns[0].keys()))
        writer.writeheader()
        writer.writerows(txns)
    print(f"  [1/3] Generated {gw_path.name} (Non-standard Gateway headers: Transaction_Ref, Billed_Total_Amount, etc.)")

    # Save 2. Unstructured Bank Statement
    bank_path = test_dir / "unstructured_icici_bank_feed.csv"
    with open(bank_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(payouts[0].keys()))
        writer.writeheader()
        writer.writerows(payouts)
    print(f"  [2/3] Generated {bank_path.name} (Non-standard Bank headers: Chq_Ref_No, Txn_Remarks, Credit_Amount, etc.)")

    # Save 3. Unstructured ERP Ledger
    erp_path = test_dir / "unstructured_erp_journal.csv"
    with open(erp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(journals[0].keys()))
        writer.writeheader()
        writer.writerows(journals)
    print(f"  [3/3] Generated {erp_path.name} (Non-standard ERP headers: Voucher_No, Net_Journal_Value, Source_Doc_Number, etc.)")


if __name__ == "__main__":
    generate_unstructured_test_suite()
    print("\n[OK] 3 Unstructured Test Documents Ready in: test_unstructured_samples/")
