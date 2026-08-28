"""Generate exactly 6 synchronized demo files (3 CSVs + 3 matching PDFs) for ARGUS CONTROL."""

from __future__ import annotations

import csv
import decimal
import random
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
demo_dir = root_dir / "demo_samples"

RANDOM_SEED = 42
random.seed(RANDOM_SEED)

START_DATE = datetime(2026, 3, 1, 9, 0, 0, tzinfo=timezone.utc)
ACCOUNT_CLEARING = "2100-PAYMENTS-CLEARING"


def generate_clean_6_files() -> None:
    # 0. Clean demo_samples directory
    if demo_dir.exists():
        shutil.rmtree(demo_dir)
    demo_dir.mkdir(parents=True, exist_ok=True)

    print("[ARGUS] Generating exactly 6 synchronized demo files (3 CSVs + 3 PDFs)...")

    num_payments = 520
    num_settlements = 26  # 20 payments per settlement batch

    payments: list[dict[str, str]] = []
    bank_entries: list[dict[str, str]] = []
    ledger_entries: list[dict[str, str]] = []

    amount_tiers = [
        499.00, 799.00, 999.00, 1250.00, 1499.00, 1999.00, 2499.00,
        3499.00, 4999.00, 7500.00, 9999.00, 14500.00, 24999.00
    ]

    settlement_buckets: dict[int, list[dict[str, str]]] = {i: [] for i in range(1, num_settlements + 1)}

    # 1. Generate 520 Payments + 520 Matching ERP Ledger Entries
    for i in range(1, num_payments + 1):
        pay_id = f"pay_DEMO_{i:04d}"
        ord_id = f"order_DEMO_{i:04d}"

        # Assign to a settlement batch (20 payments per batch)
        batch_idx = min((i - 1) // 20 + 1, num_settlements)
        stl_id = f"stl_DEMO_SETTLE_{batch_idx:02d}"

        delta_mins = (i * 35) % (30 * 24 * 60)
        captured_time = START_DATE + timedelta(minutes=delta_mins)
        captured_str = captured_time.strftime("%Y-%m-%dT%H:%M:%SZ")

        gross_amt = decimal.Decimal(str(random.choice(amount_tiers)))
        # Standard Razorpay 2.0% MDR + 18% GST on MDR
        fee_amt = (gross_amt * decimal.Decimal("0.02")).quantize(decimal.Decimal("0.01"))
        tax_amt = (fee_amt * decimal.Decimal("0.18")).quantize(decimal.Decimal("0.01"))
        net_amt = gross_amt - fee_amt - tax_amt

        p_row = {
            "payment_id": pay_id,
            "order_id": ord_id,
            "status": "CAPTURED",
            "currency": "INR",
            "gross_amount": f"{gross_amt:.2f}",
            "fee_amount": f"{fee_amt:.2f}",
            "tax_amount": f"{tax_amt:.2f}",
            "captured_at_utc": captured_str,
            "settlement_id": stl_id,
        }
        payments.append(p_row)
        settlement_buckets[batch_idx].append(p_row)

        # Corresponding ERP Ledger entry for each payment (Net amount in 2100-PAYMENTS-CLEARING)
        ledger_entries.append({
            "ledger_entry_id": f"led_PAY_{i:04d}",
            "account_code": ACCOUNT_CLEARING,
            "accounting_date": captured_str[:10],
            "currency": "INR",
            "signed_amount": f"{net_amt:.2f}",
            "source_reference": pay_id,
            "source_type": "PAYMENT",
            "description": f"Payment captured {pay_id}",
            "entry_origin": "IMPORTED",
        })

    # 2. Generate Bank Deposits
    for b_idx in range(1, num_settlements + 1):
        bucket = settlement_buckets[b_idx]
        if not bucket:
            continue

        stl_id = f"stl_DEMO_SETTLE_{b_idx:02d}"
        utr_id = f"UTR_RZP_202603_{b_idx:03d}"

        gross_total = sum(decimal.Decimal(r["gross_amount"]) for r in bucket)
        fee_total = sum(decimal.Decimal(r["fee_amount"]) for r in bucket)
        tax_total = sum(decimal.Decimal(r["tax_amount"]) for r in bucket)
        net_total = gross_total - fee_total - tax_total

        stl_time = START_DATE + timedelta(days=b_idx)

        bank_entries.append({
            "bank_entry_id": f"bnk_STL_{b_idx:03d}",
            "posted_at_utc": (stl_time + timedelta(hours=1)).strftime("%Y-%m-%dT17:00:00Z"),
            "value_date": stl_time.strftime("%Y-%m-%d"),
            "currency": "INR",
            "signed_amount": f"{net_total:.2f}",
            "narration": f"CMS/RAZORPAY NODAL SETTLEMENT/{stl_id}",
            "utr": utr_id,
            "account_fingerprint": "acc_hdfc_corp_001",
        })

    # -------------------------------------------------------------
    # 1. Razorpay Payments CSV & PDF
    # -------------------------------------------------------------
    pay_csv_path = demo_dir / "razorpay_payments_march2026.csv"
    with open(pay_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(payments[0].keys()))
        writer.writeheader()
        writer.writerows(payments)
    print(f"  [1/6] Created {pay_csv_path.name} ({len(payments)} rows)")

    pay_pdf_path = demo_dir / "razorpay_payments_statement_sample.pdf"
    # Create multi-page PDF with all 520 payment records
    _create_pdf_table(
        pdf_path=pay_pdf_path,
        title="RAZORPAY MERCHANT PAYMENT TRANSACTION STATEMENT",
        subtitle="Merchant ID: MID_DEMO_2026_01  |  Period: MARCH 2026  |  Currency: INR",
        headers="PAYMENT ID        ORDER ID          STATUS     GROSS INR     FEE INR     SETTLEMENT ID",
        rows=[
            f"{p['payment_id']}      {p['order_id']}     CAPTURED   {float(p['gross_amount']):>10,.2f}  {float(p['fee_amount']):>8,.2f}   {p['settlement_id']}"
            for p in payments
        ],
        footer="TOTAL TRANSACTIONS: 520  |  STATEMENT VERIFIED FOR 5-WAY RECONCILIATION",
    )
    print(f"  [2/6] Created {pay_pdf_path.name} ({len(payments)} rows)")

    # -------------------------------------------------------------
    # 2. HDFC Bank Statement CSV & PDF
    # -------------------------------------------------------------
    bank_csv_path = demo_dir / "hdfc_bank_statement_march2026.csv"
    with open(bank_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(bank_entries[0].keys()))
        writer.writeheader()
        writer.writerows(bank_entries)
    print(f"  [3/6] Created {bank_csv_path.name} ({len(bank_entries)} rows)")

    bank_pdf_path = demo_dir / "hdfc_bank_statement_sample.pdf"
    _create_pdf_table(
        pdf_path=bank_pdf_path,
        title="HDFC BANK LIMITED - CORPORATE ACCOUNT STATEMENT MARCH 2026",
        subtitle="Account: 50200089124419  |  Currency: INR  |  Branch: KORAMANGALA  |  IFSC: HDFC0000053",
        headers="DATE         TRANSACTION DETAILS                                 UTR NUMBER              CREDIT INR",
        rows=[
            f"{b['value_date']}  {b['narration']:<50}  {b['utr']:<20}  {float(b['signed_amount']):>12,.2f}"
            for b in bank_entries
        ],
        footer="CLOSING BALANCE: INR 12,84,590.20  |  STATEMENT VERIFIED BY HDFC BANK LTD",
    )
    print(f"  [4/6] Created {bank_pdf_path.name} ({len(bank_entries)} rows)")

    # -------------------------------------------------------------
    # 3. ERP Merchant Ledger CSV & PDF
    # -------------------------------------------------------------
    ledger_csv_path = demo_dir / "erp_merchant_ledger_march2026.csv"
    with open(ledger_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(ledger_entries[0].keys()))
        writer.writeheader()
        writer.writerows(ledger_entries)
    print(f"  [5/6] Created {ledger_csv_path.name} ({len(ledger_entries)} rows)")

    ledger_pdf_path = demo_dir / "erp_merchant_ledger_march2026.pdf"
    _create_pdf_table(
        pdf_path=ledger_pdf_path,
        title="ERP GENERAL LEDGER - BANK CLEARING JOURNAL REPORT",
        subtitle="Company: DEMO STORE PVT LTD  |  Account: 2100-PAYMENTS-CLEARING  |  Period: MARCH 2026",
        headers="VOUCHER ID    DATE        ACCOUNT CODE             AMOUNT INR    SOURCE REF       ORIGIN",
        rows=[
            f"{l['ledger_entry_id']}  {l['accounting_date']}  {l['account_code']:<23}  {float(l['signed_amount']):>10,.2f}  {l['source_reference']:<15}  IMPORTED"
            for l in ledger_entries
        ],
        footer="TOTAL JOURNAL POSTINGS: 520  |  AUDITED BY FINANCE CONTROLLER",
    )
    print(f"  [6/6] Created {ledger_pdf_path.name} ({len(ledger_entries)} rows)")


def _create_pdf_table(
    pdf_path: Path,
    title: str,
    subtitle: str,
    headers: str,
    rows: list[str],
    footer: str,
) -> None:
    lines = [
        "%PDF-1.4",
        "1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj",
        "2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj",
        "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj",
    ]

    stream_content = [
        "BT",
        "/F1 13 Tf",
        "35 805 Td",
        f"({title}) Tj",
        "/F1 8.5 Tf",
        "0 -18 Td",
        f"({subtitle}) Tj",
        "0 -22 Td",
        f"({headers}) Tj",
        "0 -10 Td",
        "(---------------------------------------------------------------------------------------------------------) Tj",
    ]

    for r in rows:
        stream_content.append("0 -16 Td")
        stream_content.append(f"({r}) Tj")

    stream_content.append("0 -20 Td")
    stream_content.append("(---------------------------------------------------------------------------------------------------------) Tj")
    stream_content.append("0 -14 Td")
    stream_content.append(f"({footer}) Tj")
    stream_content.append("ET")

    stream_bytes = "\n".join(stream_content).encode("latin-1", errors="replace")

    lines.append(f"4 0 obj\n<< /Length {len(stream_bytes)} >>\nstream")
    lines.append("\n".join(stream_content))
    lines.append("endstream\nendobj")
    lines.append("5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>\nendobj")
    lines.append("xref\n0 6\n0000000000 65535 f \n0000000010 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000224 00000 n \n0000001200 00000 n \ntrailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n1350\n%%EOF")

    pdf_path.write_bytes("\n".join(lines).encode("latin-1", errors="replace"))


if __name__ == "__main__":
    generate_clean_6_files()
    print("\n[OK] EXACTLY 6 FILES READY IN demo_samples/")
