"""Failure Laboratory package (PRD Phase 6).

Deterministic event-failure injection, replay diagnostics, and idempotency verification.
"""

from app.failure_lab.injector import (
    EventFailureInjector,
    FailureInjectionResult,
    FailureType,
)
from app.failure_lab.replay import ReplayDiagnostics, ReplayReport

__all__ = [
    "EventFailureInjector",
    "FailureInjectionResult",
    "FailureType",
    "ReplayDiagnostics",
    "ReplayReport",
]
