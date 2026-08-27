"""Generate realistic demo CSV and PDF files for the ARGUS CONTROL submission video."""

from __future__ import annotations

import csv
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
demo_dir = root_dir / "demo_samples"
demo_dir.mkdir(exist_ok=True)


def create_sample_csvs() -> None:
    # 1. Payments CSV
    payments_path = demo_dir / "razorpay_payments_march2026.csv"
    with open(payments_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "payment_id",
            "order_id",
            "status",
            "currency",
            "gross_amount",
            "fee_amount",
            "tax_amount",
            "captured_at_utc",
            "settlement_id",
        ])
        sample_payments = [
            ("pay_DEMO_001", "order_DEMO_001", "CAPTURED", "INR", "1500.00", "30.00", "5.40", "2026-03-02T10:15:22Z", "stl_DEMO_SETTLE_01"),
            ("pay_DEMO_002", "order_DEMO_002", "CAPTURED", "INR", "4200.00", "84.00", "15.12", "2026-03-02T10:18:45Z", "stl_DEMO_SETTLE_01"),
            ("pay_DEMO_003", "order_DEMO_003", "CAPTURED", "INR", "890.00", "17.80", "3.20", "2026-03-02T10:22:10Z", "stl_DEMO_SETTLE_01"),
            ("pay_DEMO_004", "order_DEMO_004", "CAPTURED", "INR", "12500.00", "250.00", "45.00", "2026-03-02T10:30:00Z", "stl_DEMO_SETTLE_01"),
            ("pay_DEMO_005", "order_DEMO_005", "CAPTURED", "INR", "2300.00", "46.00", "8.28", "2026-03-02T10:45:12Z", "stl_DEMO_SETTLE_01"),
            ("pay_DEMO_006", "order_DEMO_006", "CAPTURED", "INR", "6700.00", "134.00", "24.12", "2026-03-02T11:05:00Z", "stl_DEMO_SETTLE_02"),
            ("pay_DEMO_007", "order_DEMO_007", "CAPTURED", "INR", "3100.00", "62.00", "11.16", "2026-03-02T11:15:30Z", "stl_DEMO_SETTLE_02"),
            ("pay_DEMO_008", "order_DEMO_008", "CAPTURED", "INR", "950.00", "19.00", "3.42", "2026-03-02T11:40:19Z", "stl_DEMO_SETTLE_02"),
        ]
        for row in sample_payments:
            writer.writerow(row)

    print(f"  [OK] Generated {payments_path.name}")

    # 2. Settlements CSV (matches AdapterSpec columns)
    settlements_path = demo_dir / "razorpay_settlements_march2026.csv"
    with open(settlements_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "settlement_id",
            "settled_at_utc",
            "window_start_utc",
            "window_end_utc",
            "status",
            "currency",
            "gross_credit",
            "fee_amount",
            "tax_amount",
            "adjustment_amount",
            "net_amount",
            "utr",
        ])
        writer.writerow([
            "stl_DEMO_SETTLE_01",
            "2026-03-02T16:00:00Z",
            "2026-03-02T10:00:00Z",
            "2026-03-02T16:00:00Z",
            "PROCESSED",
            "INR",
            "21413.80",
            "427.80",
            "75.00",
            "0.00",
            "20911.00",
            "UTR_RZP_20260302_001",
        ])
        writer.writerow([
            "stl_DEMO_SETTLE_02",
            "2026-03-02T17:00:00Z",
            "2026-03-02T11:00:00Z",
            "2026-03-02T17:00:00Z",
            "PROCESSED",
            "INR",
            "10790.00",
            "215.00",
            "38.70",
            "0.00",
            "10536.30",
            "UTR_RZP_20260302_002",
        ])

    print(f"  [OK] Generated {settlements_path.name}")

    # 3. Bank Statement CSV (matches AdapterSpec columns)
    bank_path = demo_dir / "hdfc_bank_statement_march2026.csv"
    with open(bank_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "bank_entry_id",
            "posted_at_utc",
            "value_date",
            "currency",
            "signed_amount",
            "narration",
            "utr",
            "account_fingerprint",
        ])
        writer.writerow([
            "bnk_DEMO_001",
            "2026-03-02T16:30:00Z",
            "2026-03-02",
            "INR",
            "20911.00",
            "CMS/RAZORPAY SETTLEMENT/stl_DEMO_SETTLE_01",
            "UTR_RZP_20260302_001",
            "acc_hdfc_corp_001",
        ])
        writer.writerow([
            "bnk_DEMO_002",
            "2026-03-02T17:35:00Z",
            "2026-03-02",
            "INR",
            "10536.30",
            "CMS/RAZORPAY SETTLEMENT/stl_DEMO_SETTLE_02",
            "UTR_RZP_20260302_002",
            "acc_hdfc_corp_001",
        ])

    print(f"  [OK] Generated {bank_path.name}")

    # 4. Merchant ERP Ledger Entries CSV (matches AdapterSpec columns)
    ledger_path = demo_dir / "erp_merchant_ledger_march2026.csv"
    with open(ledger_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "ledger_entry_id",
            "account_code",
            "accounting_date",
            "currency",
            "signed_amount",
            "source_reference",
            "source_type",
            "description",
            "entry_origin",
        ])
        writer.writerow([
            "led_DEMO_001",
            "1100-HDFC-BANK",
            "2026-03-02",
            "INR",
            "20911.00",
            "pay_DEMO_001",
            "PAYMENT",
            "Merchant settlement payout 01",
            "IMPORTED",
        ])
        writer.writerow([
            "led_DEMO_002",
            "1100-HDFC-BANK",
            "2026-03-02",
            "INR",
            "10536.30",
            "pay_DEMO_006",
            "PAYMENT",
            "Merchant settlement payout 02",
            "IMPORTED",
        ])

    print(f"  [OK] Generated {ledger_path.name}")


def create_sample_pdf() -> None:
    """Generate a clean, standalone PDF document for HDFC Bank Statement demo."""
    pdf_path = demo_dir / "hdfc_bank_statement_sample.pdf"

    pdf_content = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj
4 0 obj
<< /Length 750 >>
stream
BT
/F1 16 Tf
50 780 Td
(HDFC BANK LIMITED - CORPORATE ACCOUNT STATEMENT) Tj
/F1 10 Tf
0 -25 Td
(Account: 50200089124419  |  Currency: INR  |  Branch: KORAMANGALA BANGALORE) Tj
0 -15 Td
(Statement Period: 01-MAR-2026 to 03-MAR-2026  |  IFSC: HDFC0000053) Tj
0 -30 Td
(DATE         TRANSACTION DETAILS                     UTR NUMBER              CREDIT (INR)) Tj
0 -15 Td
(----------------------------------------------------------------------------------------------------) Tj
0 -20 Td
(02-MAR-2026  CMS/RAZORPAY SETTLEMENT/stl_DEMO_01     UTR_RZP_20260302_001     20,911.00) Tj
0 -20 Td
(02-MAR-2026  CMS/RAZORPAY SETTLEMENT/stl_DEMO_02     UTR_RZP_20260302_002     10,536.30) Tj
0 -20 Td
(02-MAR-2026  NEFT INWARD CR/ORDER_PAY_DEMO_004       UTR_NEFT_992188410       12,500.00) Tj
0 -20 Td
(02-MAR-2026  UPI CR/PAY_DEMO_005/MERCHANT PAYOUT     UTR_UPI_771992019         2,300.00) Tj
0 -35 Td
(----------------------------------------------------------------------------------------------------) Tj
0 -15 Td
(TOTAL CREDITED: INR 46,247.30  |  CLOSING BALANCE: INR 1,84,590.20) Tj
ET
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
xref
0 6
0000000000 65535 f 
0000000010 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000224 00000 n 
0000001026 00000 n 
trailer
<< /Size 6 /Root 1 0 R >>
startxref
1108
%%EOF
"""
    with open(pdf_path, "wb") as f:
        f.write(pdf_content)

    print(f"  [OK] Generated {pdf_path.name}")


if __name__ == "__main__":
    print("[ARGUS] Generating sample demo datasets...")
    create_sample_csvs()
    create_sample_pdf()
    print(f"[ARGUS] All demo files generated in: {demo_dir}")
