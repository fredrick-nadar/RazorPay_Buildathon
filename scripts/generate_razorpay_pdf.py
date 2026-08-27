"""Generate additional sample files for demo."""

from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
demo_dir = root_dir / "demo_samples"


def add_razorpay_pdf() -> None:
    pdf_path = demo_dir / "razorpay_settlement_sheet_march2026.pdf"

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
<< /Length 650 >>
stream
BT
/F1 16 Tf
50 780 Td
(RAZORPAY MERCHANT SETTLEMENT ADVICE) Tj
/F1 10 Tf
0 -25 Td
(Merchant: ARGUS DEMO STORE  |  MID: mid_DEMO_2026  |  Currency: INR) Tj
0 -15 Td
(Settlement Batch: stl_DEMO_SETTLE_01  |  Settled Date: 02-MAR-2026 16:00 UTC) Tj
0 -30 Td
(PAYMENT ID       ORDER ID          GROSS (INR)   MDR FEE (2%)  GST (18%)   NET (INR)) Tj
0 -15 Td
(-----------------------------------------------------------------------------------------) Tj
0 -20 Td
(pay_DEMO_001     order_DEMO_001    1,500.00      30.00         5.40        1,464.60) Tj
0 -20 Td
(pay_DEMO_002     order_DEMO_002    4,200.00      84.00         15.12       4,100.88) Tj
0 -20 Td
(pay_DEMO_003     order_DEMO_003      890.00      17.80          3.20         869.00) Tj
0 -20 Td
(pay_DEMO_004     order_DEMO_004   12,500.00     250.00         45.00      12,205.00) Tj
0 -20 Td
(pay_DEMO_005     order_DEMO_005    2,300.00      46.00          8.28       2,245.72) Tj
0 -35 Td
(-----------------------------------------------------------------------------------------) Tj
0 -15 Td
(TOTAL SETTLED: INR 20,911.00  |  BANK UTR: UTR_RZP_20260302_001) Tj
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
0000000926 00000 n 
trailer
<< /Size 6 /Root 1 0 R >>
startxref
1008
%%EOF
"""
    with open(pdf_path, "wb") as f:
        f.write(pdf_content)

    print(f"  [OK] Generated {pdf_path.name}")


if __name__ == "__main__":
    add_razorpay_pdf()
