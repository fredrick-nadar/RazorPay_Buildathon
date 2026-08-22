"""Authority classification (PRD 11.1).

The MVP authority policy is deliberately simple synthetic merchant policy for
the demo — it is not Razorpay policy:

- a verified explanation with no ledger delta becomes ``VERIFIED_RESOLVED``;
- every non-zero ledger delta becomes ``APPROVAL_REQUIRED``;
- ambiguous or inconclusive verification becomes ``UNRESOLVED``;
- failed verification becomes ``VERIFICATION_FAILED``.

There is no confidence input: a model or human assertion can never override
a deterministic verifier outcome here (PRD 5.17).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import CaseStatus, VerifierStatus

AUTHORITY_POLICY_ID = "authority-policy-v1"


@dataclass(frozen=True)
class AuthorityDecision:
    """Resulting case status plus approval requirement under the MVP policy."""

    decision: CaseStatus
    requires_approval: bool
    policy_id: str
    rationale: str


def classify_authority(
    verifier_status: VerifierStatus, proposed_delta_paise: int | None
) -> AuthorityDecision:
    if verifier_status == VerifierStatus.PASS:
        if proposed_delta_paise is None:
            raise ValueError("a PASS verifier result must carry a code-derived delta")
        if proposed_delta_paise == 0:
            return AuthorityDecision(
                decision=CaseStatus.VERIFIED_RESOLVED,
                requires_approval=False,
                policy_id=AUTHORITY_POLICY_ID,
                rationale="verified explanation with no ledger delta (synthetic merchant policy)",
            )
        return AuthorityDecision(
            decision=CaseStatus.APPROVAL_REQUIRED,
            requires_approval=True,
            policy_id=AUTHORITY_POLICY_ID,
            rationale=(
                "verified explanation with a non-zero ledger delta requires human approval "
                "(synthetic merchant policy)"
            ),
        )
    if verifier_status == VerifierStatus.INCONCLUSIVE:
        return AuthorityDecision(
            decision=CaseStatus.UNRESOLVED,
            requires_approval=False,
            policy_id=AUTHORITY_POLICY_ID,
            rationale="ambiguous or inconclusive verification stays unresolved",
        )
    if verifier_status == VerifierStatus.FAIL:
        return AuthorityDecision(
            decision=CaseStatus.VERIFICATION_FAILED,
            requires_approval=False,
            policy_id=AUTHORITY_POLICY_ID,
            rationale="deterministic constraints failed; no correction may be proposed",
        )
    raise ValueError(f"unknown verifier status {verifier_status!r}")
