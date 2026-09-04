"""One absolute monotonic deadline per investigated case.

The live acceptance failure was a timeout-hierarchy inversion: a 30 s
whole-case watchdog could fire before a single 45 s HTTP attempt returned, so
no provider ever completed a turn and fallback never started.

The fix is one absolute deadline, created once per case and threaded through
every model turn, retry, provider attempt and fallback. It is deliberately
immutable and offers no way to reset, extend or renew itself: a tool call or a
retry cannot buy more wall time.

    per-attempt cap  <  model-turn window  <  total case deadline

``monotonic`` is used throughout, so a system clock change cannot lengthen or
shorten a deadline.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

# An attempt shorter than this cannot plausibly complete a TLS handshake plus a
# model response, so the remaining time is better spent failing cleanly.
MIN_VIABLE_ATTEMPT_S = 1.5


@dataclass(frozen=True)
class Deadline:
    """An absolute monotonic instant, plus the reserve kept for bookkeeping.

    ``safety_reserve_s`` is withheld from every attempt so that a provider
    failure can still be classified, recorded and returned before the outer
    watchdog fires. Without it, an attempt sized to the exact remaining time
    would always lose the race to the watchdog.
    """

    expires_at: float
    safety_reserve_s: float
    # Below this, the remaining time cannot plausibly complete a request, so it
    # is better spent failing cleanly. Configurable for fast offline tests and
    # for deployments with very different latency.
    min_attempt_s: float = MIN_VIABLE_ATTEMPT_S

    @classmethod
    def after(
        cls,
        budget_s: float,
        *,
        safety_reserve_s: float,
        now: float | None = None,
        min_attempt_s: float = MIN_VIABLE_ATTEMPT_S,
    ) -> Deadline:
        """Create a deadline ``budget_s`` from now."""
        if budget_s <= 0:
            raise ValueError(f"deadline budget must be positive, got {budget_s}")
        if safety_reserve_s < 0:
            raise ValueError(f"safety reserve must not be negative, got {safety_reserve_s}")
        if min_attempt_s <= 0:
            raise ValueError(f"minimum attempt must be positive, got {min_attempt_s}")
        started = time.monotonic() if now is None else now
        return cls(
            expires_at=started + budget_s,
            safety_reserve_s=safety_reserve_s,
            min_attempt_s=min_attempt_s,
        )

    def remaining_s(self, now: float | None = None) -> float:
        """Wall time left, never negative."""
        current = time.monotonic() if now is None else now
        return max(0.0, self.expires_at - current)

    def expired(self, now: float | None = None) -> bool:
        return self.remaining_s(now) <= 0.0

    def attempt_timeout(
        self,
        cap_s: float,
        now: float | None = None,
        reserve_extra_s: float = 0.0,
    ) -> float | None:
        """Timeout for the next attempt, or ``None`` when none should start.

        This is the value that must reach ``urlopen``: the smaller of the
        configured per-attempt cap and the time actually left after holding
        back the safety reserve and any caller-supplied reservation.

        ``reserve_extra_s`` is how the chain reserves a usable attempt for the
        providers that come after the current one. Without it, a first
        provider's attempts consume the window its fallback needs.
        """
        if cap_s <= 0:
            raise ValueError(f"attempt cap must be positive, got {cap_s}")
        if reserve_extra_s < 0:
            raise ValueError(f"extra reserve must not be negative, got {reserve_extra_s}")
        usable = self.remaining_s(now) - self.safety_reserve_s - reserve_extra_s
        if usable < self.min_attempt_s:
            return None
        # Never exceed the cap, and never return less than the stated minimum:
        # refusing an attempt for being under a minimum the cap cannot reach
        # would be incoherent, so the cap is validated against the minimum by
        # the execution policy instead.
        return min(cap_s, usable)

    def sub_deadline(self, window_s: float, now: float | None = None) -> Deadline:
        """A nested window that can end earlier than this deadline, never later.

        Used for the per-turn window inside a case deadline. Because the result
        is clamped to ``self``, no number of turns can extend the case budget.
        """
        if window_s <= 0:
            raise ValueError(f"window must be positive, got {window_s}")
        current = time.monotonic() if now is None else now
        return Deadline(
            expires_at=min(self.expires_at, current + window_s),
            safety_reserve_s=self.safety_reserve_s,
            min_attempt_s=self.min_attempt_s,
        )

    def sleep_budget(
        self,
        requested_s: float,
        now: float | None = None,
        reserve_extra_s: float = 0.0,
    ) -> float:
        """Clamp a backoff sleep so it cannot overrun the deadline or a reserve.

        ``reserve_extra_s`` is the fallback reservation. Without subtracting it
        here, a retry backoff quietly ate part of the window promised to the
        next provider, so the fallback received less than a full attempt.
        """
        if reserve_extra_s < 0:
            raise ValueError(f"extra reserve must not be negative, got {reserve_extra_s}")
        usable = (
            self.remaining_s(now) - self.safety_reserve_s - self.min_attempt_s - reserve_extra_s
        )
        return max(0.0, min(requested_s, usable))


__all__ = ["Deadline", "MIN_VIABLE_ATTEMPT_S"]
