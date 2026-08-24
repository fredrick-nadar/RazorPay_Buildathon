"""Bounded AI investigator (PRD 10, Phase 4).

The investigator proposes structured hypotheses for UNRESOLVED or
VERIFICATION_FAILED cases. It has read-only tools; backend engine code routes
every hypothesis through the Phase 3 verifier and performs dry-run only after
verifier PASS.  The model cannot approve, apply, resolve, or write ledger data.

Phase 4 ships with ``FakeProvider`` only — zero external dependencies, zero
model API calls, zero secrets.
"""

from app.investigator.engine import InvestigationResult, investigate_cases
from app.investigator.provider import FakeProvider, InvestigatorProvider

__all__ = [
    "FakeProvider",
    "InvestigationResult",
    "InvestigatorProvider",
    "investigate_cases",
]
