"""Automated MDR & GST Fee Variance Reconciler for ARGUS CONTROL.

Audits gateway fee deductions and GST taxes down to exact signed integer paise
against contractual merchant rate cards. Zero float arithmetic.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.money import require_paise
from app.persistence.database import Database


class FeeAuditItem(BaseModel):
    record_id: str
    record_type: str  # payment, settlement, refund
    gross_amount_paise: int
    contractual_mdr_bps: int = Field(
        default=200, description="Contractual MDR in basis points (200 = 2.00%)"
    )
    contractual_gst_bps: int = Field(
        default=1800, description="GST on fees in basis points (1800 = 18.00%)"
    )
    expected_fee_paise: int
    expected_gst_paise: int
    expected_total_deduction_paise: int
    actual_fee_paise: int
    actual_gst_paise: int
    actual_total_deduction_paise: int
    variance_paise: int = Field(
        description="Positive means merchant was overcharged; negative means undercharged"
    )
    is_anomaly: bool
    anomaly_reason: str | None = None


class RunFeeAuditSummary(BaseModel):
    run_id: str
    total_gmv_paise: int
    total_expected_fee_paise: int
    total_actual_fee_paise: int
    total_fee_variance_paise: int
    total_expected_gst_paise: int
    total_actual_gst_paise: int
    total_gst_variance_paise: int
    net_leakage_paise: int
    audited_records_count: int
    anomalous_records_count: int
    items: list[FeeAuditItem] = Field(default_factory=list)


def audit_run_fees(
    db: Database,
    run_id: str,
    contractual_mdr_bps: int = 200,
    contractual_gst_bps: int = 1800,
) -> RunFeeAuditSummary:
    """Audit fee and tax deductions for all normalized payments in a run.

    Uses exact integer paise arithmetic:
        expected_mdr = (gross_paise * mdr_bps + 5000) // 10000 (standard rounding to nearest paise)
        expected_gst = (expected_mdr * gst_bps + 5000) // 10000
    """
    payments = db.query_all(
        "SELECT payment_id, gross_amount_paise, fee_paise, tax_paise "
        "FROM norm_payments "
        "WHERE run_id = ?",
        (run_id,),
    )

    items: list[FeeAuditItem] = []
    total_gmv = 0
    total_exp_fee = 0
    total_act_fee = 0
    total_exp_gst = 0
    total_act_gst = 0

    for row in payments:
        pid = str(row["payment_id"])
        gross_paise = require_paise(row["gross_amount_paise"])
        act_fee = require_paise(row["fee_paise"]) if row["fee_paise"] is not None else 0
        act_gst = require_paise(row["tax_paise"]) if row["tax_paise"] is not None else 0

        # Exact integer basis point arithmetic
        exp_fee = (gross_paise * contractual_mdr_bps + 5000) // 10000
        exp_gst = (exp_fee * contractual_gst_bps + 5000) // 10000

        exp_total = exp_fee + exp_gst
        act_total = act_fee + act_gst
        variance = act_total - exp_total

        # Anomaly if variance > 50 paise (tolerance for micro rounding)
        is_anomaly = abs(variance) > 50
        reason = None
        if is_anomaly:
            if variance > 0:
                reason = (
                    f"MDR overcharge of {variance} paise detected "
                    f"(Actual: {act_total}p vs Exp: {exp_total}p)"
                )
            else:
                reason = f"MDR concession/discount of {abs(variance)} paise detected"

        total_gmv += gross_paise
        total_exp_fee += exp_fee
        total_act_fee += act_fee
        total_exp_gst += exp_gst
        total_act_gst += act_gst

        items.append(
            FeeAuditItem(
                record_id=pid,
                record_type="payment",
                gross_amount_paise=gross_paise,
                contractual_mdr_bps=contractual_mdr_bps,
                contractual_gst_bps=contractual_gst_bps,
                expected_fee_paise=exp_fee,
                expected_gst_paise=exp_gst,
                expected_total_deduction_paise=exp_total,
                actual_fee_paise=act_fee,
                actual_gst_paise=act_gst,
                actual_total_deduction_paise=act_total,
                variance_paise=variance,
                is_anomaly=is_anomaly,
                anomaly_reason=reason,
            )
        )

    anomalies_count = sum(1 for i in items if i.is_anomaly)
    net_leakage = (total_act_fee + total_act_gst) - (total_exp_fee + total_exp_gst)

    return RunFeeAuditSummary(
        run_id=run_id,
        total_gmv_paise=total_gmv,
        total_expected_fee_paise=total_exp_fee,
        total_actual_fee_paise=total_act_fee,
        total_fee_variance_paise=total_act_fee - total_exp_fee,
        total_expected_gst_paise=total_exp_gst,
        total_actual_gst_paise=total_act_gst,
        total_gst_variance_paise=total_act_gst - total_exp_gst,
        net_leakage_paise=net_leakage,
        audited_records_count=len(items),
        anomalous_records_count=anomalies_count,
        items=items[:100],  # Return up to top 100 items in API response
    )
