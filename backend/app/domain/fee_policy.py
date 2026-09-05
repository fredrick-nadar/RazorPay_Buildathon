"""Configured, versioned SYNTHETIC merchant fee policy (PRD 5.11, 13.3).

The MDR and GST rates ARGUS audits against are a demo merchant agreement
authored for this prototype. They are not Razorpay's published pricing, not a
universal rate card, and not a statement about any real merchant contract.

Before this module the rates arrived as request query parameters with silent
defaults, so any caller could dictate the basis of a leakage figure and the
response carried no identity for the policy it had used. A fee audit is a
financial claim: its basis has to be configured, versioned, labelled synthetic,
and reported back with the numbers it produced.

Rates are integer basis points so every expected amount stays exact integer
paise; no float participates.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from app.config import Settings

__all__ = [
    "FEE_POLICY_VERSION",
    "FeePolicy",
    "resolve_fee_policy",
]

# Bump when a rate, tolerance, rounding rule, or the policy identity changes.
FEE_POLICY_VERSION = "synthetic-merchant-fee-policy-v1"


@dataclass(frozen=True)
class FeePolicy:
    """One immutable, identified fee basis used by an audit."""

    policy_id: str
    policy_version: str
    mdr_bps: int
    gst_on_fee_bps: int
    tolerance_paise: int
    rounding_rule: str
    data_classification: str
    source: str
    notice: str

    @property
    def fingerprint(self) -> str:
        """Stable digest over the rate-bearing fields, for response binding."""
        material = json.dumps(
            {
                "policy_id": self.policy_id,
                "policy_version": self.policy_version,
                "mdr_bps": self.mdr_bps,
                "gst_on_fee_bps": self.gst_on_fee_bps,
                "tolerance_paise": self.tolerance_paise,
                "rounding_rule": self.rounding_rule,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "policy_fingerprint": self.fingerprint,
            "mdr_bps": self.mdr_bps,
            "gst_on_fee_bps": self.gst_on_fee_bps,
            "tolerance_paise": self.tolerance_paise,
            "rounding_rule": self.rounding_rule,
            "data_classification": self.data_classification,
            "source": self.source,
            "notice": self.notice,
        }


def resolve_fee_policy(settings: Settings) -> FeePolicy:
    """Build the active fee policy from configuration only.

    The caller cannot influence the rates. A deployment changes them through
    ``ARGUS_SYNTHETIC_MDR_BPS`` / ``ARGUS_SYNTHETIC_GST_ON_FEE_BPS`` /
    ``ARGUS_SYNTHETIC_FEE_TOLERANCE_PAISE``, and the resulting policy is
    reported with every audit so a reader can see the basis of the figures.
    """
    return FeePolicy(
        policy_id=settings.synthetic_fee_policy_id,
        policy_version=FEE_POLICY_VERSION,
        mdr_bps=settings.synthetic_mdr_bps,
        gst_on_fee_bps=settings.synthetic_gst_on_fee_bps,
        tolerance_paise=settings.synthetic_fee_tolerance_paise,
        rounding_rule="HALF_UP_TO_NEAREST_PAISE_INTEGER_BPS",
        data_classification="SYNTHETIC_ONLY",
        source="CONFIGURED_SYNTHETIC_MERCHANT_AGREEMENT",
        notice=(
            "Rates come from a synthetic demo merchant agreement configured for "
            "this prototype. They are not Razorpay published pricing and not a "
            "universal rate card."
        ),
    )
