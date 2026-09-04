"""Investigation budgets, retry limits, and timeout configuration (PRD 10.5).

All limits are configurable via ``app.config.Settings`` but have safe defaults.
Timeout enforcement on Windows uses a worker thread (not ``signal.alarm``).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from app.ai.deadline import Deadline


@dataclass(frozen=True)
class InvestigationBudget:
    """Per-case budget for one investigation attempt.

    ``max_tool_calls`` is the total number of tool dispatches the provider may
    make before the engine forces ``INVESTIGATION_FAILED``.

    ``max_total_attempts`` is the total number of *attempts* to obtain a valid
    (Pydantic-parseable, ``extra="forbid"``-clean) provider response.  The first
    attempt counts.  So ``max_total_attempts=2`` means: initial attempt + 1 retry.
    """

    max_tool_calls: int = 12
    remaining_tool_calls: int = 12
    max_total_attempts: int = 2
    remaining_attempts: int = 2
    timeout_s: float = 75.0
    # Absolute monotonic deadline for THIS case, created once and never reset.
    # ``None`` means the provider must create one from ``timeout_s``.
    deadline: Deadline | None = None
    # Grace the last-resort worker watchdog allows over the case deadline. It
    # only ever fires for a provider that ignores its deadline outright.
    watchdog_grace_s: float = 5.0

    def use_tool_call(self) -> InvestigationBudget:
        """Return a new budget with one fewer tool call."""
        return replace(self, remaining_tool_calls=self.remaining_tool_calls - 1)

    def use_attempt(self) -> InvestigationBudget:
        """Return a new budget with one fewer attempt."""
        return replace(self, remaining_attempts=self.remaining_attempts - 1)

    def with_deadline(self, deadline: Deadline) -> InvestigationBudget:
        """Attach the case deadline. Never used to lengthen an existing one."""
        return replace(self, deadline=deadline)

    @property
    def tool_calls_exhausted(self) -> bool:
        return self.remaining_tool_calls <= 0

    @property
    def attempts_exhausted(self) -> bool:
        return self.remaining_attempts <= 0

    @property
    def tool_calls_used(self) -> int:
        return self.max_tool_calls - self.remaining_tool_calls

    @property
    def attempts_used(self) -> int:
        return self.max_total_attempts - self.remaining_attempts
