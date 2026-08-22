"""Control totals and residual variance (PRD 8.5), computed from stored records.

All totals are reproducible from the accepted normalized records alone. The
unresolved absolute variance is the sum of |case.variance_paise| across every
open case - the money-equivalent of what the deterministic rules could not
explain. Sign conventions per category are documented in ``detectors.py``.
"""

from __future__ import annotations

from typing import Any

from app.domain.records import AcceptedRecords
from app.reconciliation.detectors import CaseRecord

SCOPED_ACCOUNTS = ("1100-BANK-OPERATING", "2100-PAYMENTS-CLEARING")


def control_totals(records: AcceptedRecords, cases: list[CaseRecord]) -> dict[str, Any]:
    payment_gross = sum(int(p.gross_amount_paise) for p in records.payments)
    payment_fee = sum(int(p.fee_paise) for p in records.payments)
    payment_tax = sum(int(p.tax_paise) for p in records.payments)
    payment_net = payment_gross - payment_fee - payment_tax
    refund_total = sum(int(r.refund_amount_paise) for r in records.refunds)
    settlement_net = sum(int(s.net_amount_paise) for s in records.settlements)
    bank_credit = sum(int(b.signed_amount_paise) for b in records.bank_entries)
    ledger_by_account: dict[str, int] = {}
    for entry in records.ledger_entries:
        ledger_by_account[entry.account_code] = ledger_by_account.get(entry.account_code, 0) + int(
            entry.signed_amount_paise
        )
    return {
        "payment_gross_paise": payment_gross,
        "payment_fee_paise": payment_fee,
        "payment_tax_paise": payment_tax,
        "payment_net_paise": payment_net,
        "refund_total_paise": refund_total,
        "expected_net_settlement_paise": payment_net - refund_total,
        "settlement_net_paise": settlement_net,
        "bank_credit_paise": bank_credit,
        "ledger_by_account_paise": dict(sorted(ledger_by_account.items())),
        "ledger_total_paise": sum(ledger_by_account.values()),
        "scoped_ledger_accounts": list(SCOPED_ACCOUNTS),
        "residual_abs_variance_paise": sum(abs(case.variance_paise) for case in cases),
        "affected_amount_paise": sum(case.affected_amount_paise for case in cases),
    }


def verify_match_invariants(
    matches: list[Any],
) -> list[str]:
    """Signed-contribution invariants for every stored match group.

    Aggregation groups (settlement membership) must satisfy
    ``sum(contributions) == amount_paise``. Zero-sum relationships must
    satisfy ``sum(contributions) == 0``: pair relationships with every
    contribution magnitude equal to the stored amount, and refund
    compositions with the positive components summing to the stored amount
    against the negative booking row.
    """
    from app.reconciliation.rules import (
        AGGREGATION_RELATIONSHIPS,
        R_REFUND_COMPOSITION,
        ZERO_SUM_RELATIONSHIPS,
    )

    problems: list[str] = []
    for group in matches:
        total = sum(member.signed_contribution_paise for member in group.members)
        if group.relationship_type in AGGREGATION_RELATIONSHIPS:
            if total != group.amount_paise:
                problems.append(
                    f"{group.match_id}: contributions {total} != stored total {group.amount_paise}"
                )
        elif group.relationship_type in ZERO_SUM_RELATIONSHIPS:
            if total != 0:
                problems.append(f"{group.match_id}: zero-sum group contributes {total} != 0")
                continue
            if group.rule_id == R_REFUND_COMPOSITION:
                positives = sum(
                    member.signed_contribution_paise
                    for member in group.members
                    if member.signed_contribution_paise > 0
                )
                if positives != group.amount_paise:
                    problems.append(
                        f"{group.match_id}: composition components {positives} != "
                        f"stored total {group.amount_paise}"
                    )
            else:
                for member in group.members:
                    if abs(member.signed_contribution_paise) != group.amount_paise:
                        problems.append(
                            f"{group.match_id}: member {member.record_id} "
                            f"contribution {member.signed_contribution_paise} "
                            f"magnitude differs from stored total {group.amount_paise}"
                        )
        else:
            problems.append(f"{group.match_id}: unknown relationship {group.relationship_type}")
    return problems
