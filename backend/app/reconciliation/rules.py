"""Rule registry, versions, and engine configuration (PRD 8.2, 8.3).

Rules are applied strictly strongest-first. Amounts are only ever consulted
inside rules that additionally require a unique identifier or a uniqueness
proof, so amount-only matching with multiple candidates is structurally
impossible.
"""

from __future__ import annotations

from app.domain.enums import RelationshipType

RULE_MANIFEST_VERSION = "recon-rules-v1"

# Tier 1: exact cross-file identifiers.
R_REFUND_TO_PAYMENT = "R-EXACT-REFUND-PAYMENT"
R_PAYMENT_TO_SETTLEMENT = "R-EXACT-PAYMENT-SETTLEMENT"
R_REFUND_TO_SETTLEMENT = "R-EXACT-REFUND-SETTLEMENT"
R_LEDGER_TO_SOURCE = "R-EXACT-LEDGER-SOURCE"
# Tier 2: exact UTR plus compatible amount.
R_SETTLEMENT_BANK_UTR = "R-UTR-AMOUNT-BANK"
# Tier 6: unique amount within the posting window (UTR absent).
R_SETTLEMENT_BANK_UNIQUE = "R-UNIQUE-AMOUNT-WINDOW-BANK"
# Unique refund composition for aggregate deduction rows.
R_REFUND_COMPOSITION = "R-UNIQUE-REFUND-COMPOSITION"

RULE_VERSIONS: dict[str, str] = {
    R_REFUND_TO_PAYMENT: "1",
    R_PAYMENT_TO_SETTLEMENT: "1",
    R_REFUND_TO_SETTLEMENT: "1",
    R_LEDGER_TO_SOURCE: "1",
    R_SETTLEMENT_BANK_UTR: "1",
    R_SETTLEMENT_BANK_UNIQUE: "1",
    R_REFUND_COMPOSITION: "1",
}

# Bank posting window tolerance: a credit is a candidate for a settlement
# only when posted within [window_start - 24h, window_end + 24h].
BANK_POSTING_WINDOW_S = 86_400

# Refund ledger posting window: a PROCESSED refund whose ledger posting is
# absent within this many days after creation is a missing-posting case.
REFUND_POSTING_WINDOW_DAYS = 3

# Subset-sum bound for refund composition resolution.
MAX_COMPOSITION_REFUNDS = 16

# Ledger account codes used by amount-semantics checks.
ACCOUNT_CLEARING = "2100-PAYMENTS-CLEARING"
ACCOUNT_BANK = "1100-BANK-OPERATING"


def rule_manifest() -> dict[str, dict[str, str]]:
    """Serializable manifest of every rule id and version."""
    return {rule_id: {"version": version} for rule_id, version in sorted(RULE_VERSIONS.items())}


# Consumption slots (typed, per relationship kind). Exclusivity is enforced
# inside a slot only: a payment legitimately participates in settlement
# membership, refund-parent, and ledger-source relationships simultaneously.
SLOT_SETTLEMENT_MEMBERSHIP = "SETTLEMENT_MEMBERSHIP"
SLOT_REFUND_PARENT = "REFUND_PARENT"
SLOT_BANK_CREDIT_MATCH = "BANK_CREDIT_MATCH"
SLOT_LEDGER_SOURCE_MATCH = "LEDGER_SOURCE_MATCH"
SLOT_REFUND_COMPOSITION = "REFUND_COMPOSITION"

# Match groups that aggregate many contributions into one stored total.
AGGREGATION_RELATIONSHIPS = frozenset({RelationshipType.MEMBER_OF_SETTLEMENT})
# Zero-sum relationships: contributions transfer value between the sides and
# sum to zero; the stored amount is the transfer magnitude.
ZERO_SUM_RELATIONSHIPS = frozenset(
    {
        RelationshipType.REFUND_OF_PAYMENT,
        RelationshipType.SETTLEMENT_BANK_CREDIT,
        RelationshipType.LEDGER_SOURCE,
    }
)
