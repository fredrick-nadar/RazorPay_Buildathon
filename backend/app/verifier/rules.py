"""Verifier rule registry and policy constants (PRD 9, 11.1).

Verifier rules version independently of the reconciliation rules: every proof
cites the exact verifier rule id and version that produced it, and a rule or
manifest version change makes every earlier proof stale
(:func:`app.verifier.proof.proof_stale_reasons`).

Policy constants here are synthetic merchant policy values for the demo,
labelled as such; they are not Razorpay policy (PRD 5.23).
"""

from __future__ import annotations

VERIFIER_RULE_MANIFEST_VERSION = "verify-rules-v1"

V_DUPLICATE_LEDGER = "V-DUPLICATE-LEDGER"
V_MISSING_REFUND = "V-MISSING-REFUND"
V_TIMING_WINDOW = "V-TIMING-WINDOW"
V_AMBIGUITY = "V-AMBIGUITY"

VERIFIER_RULE_VERSIONS: dict[str, str] = {
    V_DUPLICATE_LEDGER: "1",
    V_MISSING_REFUND: "1",
    V_TIMING_WINDOW: "1",
    V_AMBIGUITY: "1",
}

# Timing-window attribution tolerance (synthetic merchant policy): a ledger
# booking qualifies as an adjacent-window shift only when it lands within this
# many days of the settlement's own window.
TIMING_ADJACENCY_DAYS = 3

# Refund status eligible for the missing-posting verifier.
REFUND_STATUS_PROCESSED = "PROCESSED"


def verifier_rule_manifest() -> dict[str, dict[str, str]]:
    """Serializable manifest of every verifier rule id and version."""
    return {
        rule_id: {"version": version} for rule_id, version in sorted(VERIFIER_RULE_VERSIONS.items())
    }
