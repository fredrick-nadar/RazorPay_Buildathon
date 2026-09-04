"""Deadline-aware provider execution (cloud-reference §19). Network-free.

The live acceptance run failed because the timeout hierarchy was inverted: the
whole-case watchdog (30 s) fired before a single HTTP attempt (45 s) could
return, so no provider ever produced a completed turn, Sarvam fallback never
started, and ``actual_providers`` stayed empty.

These tests pin the corrected hierarchy:

    per-attempt cap  <  model-turn window  <  total case deadline

Every provider here is driven by a scripted transport. No test in this module
touches the network, and no test needs a real key.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.ai.base import LLMError
from app.ai.chain import AIChainError, build_chain
from app.ai.deadline import Deadline
from app.ai.policy import InvestigatorExecutionPolicy, policy_from_settings
from app.ai.selection import resolve_investigator
from app.config import Settings
from app.investigator.engine import investigate_cases
from tests.unit.test_investigator_engine import _make_duplicate_ledger_fixtures


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "ai_provider": "auto",
        "groq_api_key": "gsk_scripted_offline_only",
        "sarvam_api_key": "scripted_offline_only",
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)


def _completion(text: str) -> bytes:
    return json.dumps({"choices": [{"message": {"content": text}}]}).encode("utf-8")


class FakeClock:
    """Monotonic clock advanced only by scripted transports."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def scripted_transport(
    clock: FakeClock, script: list[tuple[float, Any]]
) -> tuple[Any, list[dict[str, Any]]]:
    """Transport that consumes wall time and then succeeds, fails, or blocks.

    Each script entry is ``(seconds_consumed, outcome)``. When the transport is
    given a shorter timeout than the entry needs, it consumes only the allowed
    time and raises a timeout, exactly as ``urlopen`` would.
    """
    calls: list[dict[str, Any]] = []
    queue = list(script)

    def transport(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes,
        timeout_s: float,
    ) -> tuple[int, bytes]:
        wants, outcome = queue.pop(0)
        calls.append({"url": url, "timeout_s": timeout_s, "wants": wants})
        if wants > timeout_s:
            clock.advance(timeout_s)
            raise LLMError("transport", "attempt timeout", retryable=True, timeout=True)
        clock.advance(wants)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, int):
            return outcome, b'{"error": "scripted status"}'
        return 200, _completion(str(outcome))

    return transport, calls


class TestTimeoutHierarchy:
    """The inverted hierarchy that broke the live run must be impossible."""

    def test_default_attempt_cap_is_below_turn_window_and_case_deadline(self) -> None:
        policy = policy_from_settings(_settings())
        assert policy.attempt_timeout_cap_s < policy.turn_deadline_s, (
            "one HTTP attempt must not be able to consume a whole model turn"
        )
        assert policy.turn_deadline_s < policy.case_deadline_s, (
            "one model turn must not be able to consume the whole case budget"
        )
        # The regression that caused the live failure, stated numerically.
        assert 10.0 <= policy.attempt_timeout_cap_s <= 12.0
        assert policy.case_deadline_s >= 70.0

    def test_configured_values_cannot_invert_the_hierarchy(self) -> None:
        """A configuration that inverts the hierarchy is clamped, not honoured."""
        policy = policy_from_settings(_settings(ai_timeout_s=45.0, investigator_timeout_s=30.0))
        assert policy.attempt_timeout_cap_s <= policy.turn_deadline_s
        assert policy.turn_deadline_s <= policy.case_deadline_s

    def test_every_deadline_value_stays_configurable(self) -> None:
        policy = policy_from_settings(
            _settings(
                ai_timeout_s=6.0,
                investigator_turn_timeout_s=18.0,
                investigator_timeout_s=40.0,
            )
        )
        assert policy.attempt_timeout_cap_s == pytest.approx(6.0)
        assert policy.turn_deadline_s == pytest.approx(18.0)
        assert policy.case_deadline_s == pytest.approx(40.0)


class TestDeadlineArithmetic:
    def test_attempt_timeout_is_bounded_by_remaining_time(self) -> None:
        clock = FakeClock()
        deadline = Deadline.after(30.0, safety_reserve_s=0.5, now=clock())
        assert deadline.attempt_timeout(cap_s=12.0, now=clock()) == pytest.approx(12.0)
        clock.advance(22.0)
        # Only 8 s left: the cap must not be used blindly.
        assert deadline.attempt_timeout(cap_s=12.0, now=clock()) == pytest.approx(7.5)

    def test_no_attempt_starts_without_safe_remaining_time(self) -> None:
        clock = FakeClock()
        deadline = Deadline.after(30.0, safety_reserve_s=0.5, now=clock())
        clock.advance(29.9)
        assert deadline.attempt_timeout(cap_s=12.0, now=clock()) is None
        assert deadline.expired(now=clock()) is False
        clock.advance(1.0)
        assert deadline.expired(now=clock()) is True

    def test_a_sub_deadline_never_outlives_its_parent(self) -> None:
        clock = FakeClock()
        case = Deadline.after(20.0, safety_reserve_s=0.5, now=clock())
        turn = case.sub_deadline(25.0, now=clock())
        assert turn.expires_at == case.expires_at
        shorter = case.sub_deadline(5.0, now=clock())
        assert shorter.expires_at < case.expires_at

    def test_a_deadline_is_never_reset(self) -> None:
        """There is no API that extends an existing deadline."""
        clock = FakeClock()
        case = Deadline.after(20.0, safety_reserve_s=0.5, now=clock())
        for name in ("reset", "extend", "renew", "refresh"):
            assert not hasattr(case, name)
        clock.advance(10.0)
        # A sub-deadline computed later still ends no later than the parent.
        assert case.sub_deadline(60.0, now=clock()).expires_at == case.expires_at


class TestFallbackWithinOneDeadline:
    def test_blocked_groq_leaves_time_for_sarvam_inside_the_case_deadline(self) -> None:
        """The live-failure scenario, now succeeding through fallback."""
        clock = FakeClock()
        # Groq blocks far longer than any attempt cap; Sarvam answers quickly.
        transport, calls = scripted_transport(clock, [(600.0, None), (1.5, '{"action": "final"}')])
        chain = build_chain(
            _settings(
                ai_timeout_s=10.0,
                investigator_timeout_s=75.0,
                ai_provider_max_attempts=1,
            ),
            transport=transport,
        )
        assert chain.member_ids == ["groq", "sarvam"]
        deadline = Deadline.after(75.0, safety_reserve_s=0.5, now=clock())

        outcome = chain.chat_with_attempts(
            "system", "user", json_mode=True, deadline=deadline, now=clock
        )

        assert outcome.response.provider_id == "sarvam"
        # Groq consumed only its capped attempt, never the whole case budget.
        assert calls[0]["timeout_s"] == pytest.approx(10.0)
        assert clock.now - 1_000.0 < 75.0
        assert [item.provider_id for item in outcome.attempts] == ["groq", "sarvam"]
        assert outcome.attempts[0].outcome == "TIMEOUT"
        assert outcome.attempts[1].outcome == "SUCCESS"

    def test_attempted_providers_include_a_timed_out_backend(self) -> None:
        clock = FakeClock()
        transport, _calls = scripted_transport(clock, [(600.0, None), (1.0, '{"action": "final"}')])
        chain = build_chain(
            _settings(
                ai_timeout_s=10.0,
                investigator_timeout_s=75.0,
                ai_provider_max_attempts=1,
            ),
            transport=transport,
        )
        outcome = chain.chat_with_attempts(
            "s", "u", deadline=Deadline.after(75.0, safety_reserve_s=0.5, now=clock()), now=clock
        )
        # Attempted is the honest superset; actual means a response came back.
        assert outcome.attempted_providers == ("groq", "sarvam")
        assert outcome.actual_provider == "sarvam"

    def test_no_provider_is_attempted_once_the_deadline_is_exhausted(self) -> None:
        clock = FakeClock()
        transport, calls = scripted_transport(clock, [(600.0, None)])
        chain = build_chain(
            _settings(
                ai_timeout_s=10.0,
                investigator_timeout_s=10.0,
                ai_provider_max_attempts=1,
                # Deadline arithmetic only; fallback reservation is covered by
                # TestFallbackReservation.
                investigator_fallback_reserve_s=0.0,
            ),
            transport=transport,
        )
        deadline = Deadline.after(10.0, safety_reserve_s=0.5, now=clock())

        with pytest.raises(AIChainError) as excinfo:
            chain.chat_with_attempts("s", "u", deadline=deadline, now=clock)

        # Groq consumed the whole budget; Sarvam was never dialled.
        assert len(calls) == 1
        outcomes = [item.outcome for item in excinfo.value.attempts]
        assert "TIMEOUT" in outcomes
        assert outcomes[-1] == "DEADLINE_EXHAUSTED"
        assert excinfo.value.attempts[-1].provider_id == "sarvam"

    def test_retry_never_sleeps_past_the_deadline(self) -> None:
        clock = FakeClock()
        transport, calls = scripted_transport(clock, [(1.0, 429), (1.0, '{"action": "final"}')])
        chain = build_chain(
            _settings(
                ai_provider="groq",
                sarvam_api_key=None,
                ai_timeout_s=10.0,
                investigator_timeout_s=75.0,
                ai_provider_max_attempts=2,
            ),
            transport=transport,
        )
        outcome = chain.chat_with_attempts(
            "s", "u", deadline=Deadline.after(75.0, safety_reserve_s=0.5, now=clock()), now=clock
        )
        assert outcome.response.provider_id == "groq"
        assert [item.outcome for item in outcome.attempts] == ["RETRYABLE_HTTP", "SUCCESS"]
        assert len(calls) == 2
        assert clock.now - 1_000.0 < 75.0


class TestAttemptMetadataSafety:
    def test_attempt_metadata_carries_no_secret_or_body_content(self) -> None:
        clock = FakeClock()
        secret = "gsk_live_secret_value_must_never_persist"
        transport, _calls = scripted_transport(clock, [(1.0, 500), (1.0, 500)])
        chain = build_chain(
            _settings(ai_provider="groq", groq_api_key=secret, ai_timeout_s=10.0),
            transport=transport,
        )
        with pytest.raises(AIChainError) as excinfo:
            chain.chat_with_attempts(
                "s",
                "u",
                deadline=Deadline.after(75.0, safety_reserve_s=0.5, now=clock()),
                now=clock,
            )
        serialized = json.dumps([item.to_json() for item in excinfo.value.attempts])
        assert secret not in serialized
        assert "scripted status" not in serialized
        assert "Authorization" not in serialized
        assert "Bearer" not in serialized
        for item in excinfo.value.attempts:
            assert item.outcome in {
                "SUCCESS",
                "TIMEOUT",
                "RETRYABLE_HTTP",
                "NON_RETRYABLE_HTTP",
                "MALFORMED_RESPONSE",
                "DEADLINE_EXHAUSTED",
                "TRANSPORT_ERROR",
            }
            assert set(item.to_json()) == {
                "provider_id",
                "model",
                "attempt",
                "outcome",
                "duration_ms",
                "status_code",
                # Distinguishes a real contact from a pre-dial refusal.
                "contacted",
            }

    def test_the_chain_error_message_is_typed_and_body_free(self) -> None:
        clock = FakeClock()
        transport, _calls = scripted_transport(clock, [(1.0, 500)])
        chain = build_chain(
            _settings(
                ai_provider="groq",
                ai_timeout_s=10.0,
                ai_provider_max_attempts=1,
            ),
            transport=transport,
        )
        with pytest.raises(AIChainError) as excinfo:
            chain.chat_with_attempts(
                "s",
                "u",
                deadline=Deadline.after(30.0, safety_reserve_s=0.5, now=clock()),
                now=clock,
            )
        assert "scripted status" not in str(excinfo.value)
        assert "gsk_" not in str(excinfo.value)


class TestPolicyFingerprint:
    def test_fingerprint_covers_policy_and_never_a_key(self) -> None:
        settings = _settings(groq_api_key="gsk_secret_never_hashed")
        policy = policy_from_settings(settings)
        described = json.dumps(policy.describe(), sort_keys=True)
        assert "gsk_secret_never_hashed" not in described
        assert "gsk_" not in described
        assert len(policy.fingerprint()) == 64

    @pytest.mark.parametrize(
        "overrides",
        [
            {"groq_investigator_model": "openai/gpt-oss-120b"},
            {"ai_timeout_s": 9.0},
            {"investigator_timeout_s": 90.0},
            {"investigator_turn_timeout_s": 20.0},
            {"ai_provider_max_attempts": 3},
            {"investigator_tool_budget": 8},
            {"ai_provider": "groq"},
        ],
    )
    def test_material_policy_change_changes_the_fingerprint(
        self, overrides: dict[str, Any]
    ) -> None:
        before = policy_from_settings(_settings()).fingerprint()
        after = policy_from_settings(_settings(**overrides)).fingerprint()
        assert before != after

    def test_changing_only_a_key_does_not_change_the_fingerprint(self) -> None:
        """The fingerprint is policy identity, not credential identity."""
        before = policy_from_settings(_settings(groq_api_key="gsk_one")).fingerprint()
        after = policy_from_settings(_settings(groq_api_key="gsk_two")).fingerprint()
        assert before == after

    def test_provider_order_is_part_of_the_fingerprint(self) -> None:
        both = policy_from_settings(_settings()).fingerprint()
        groq_only = policy_from_settings(_settings(sarvam_api_key=None)).fingerprint()
        assert both != groq_only

    def test_prompt_and_tool_protocol_versions_are_included(self) -> None:
        policy = policy_from_settings(_settings())
        described = policy.describe()
        assert described["prompt_protocol_version"]
        assert described["tool_protocol_version"]
        assert described["result_schema_version"]
        bumped = InvestigatorExecutionPolicy(
            **{**policy.as_kwargs(), "prompt_protocol_version": "prompt-v99"}
        )
        assert bumped.fingerprint() != policy.fingerprint()


class TestLegacyChatEntryPoint:
    def test_plain_chat_still_works_for_non_investigator_callers(self) -> None:
        clock = FakeClock()
        transport, calls = scripted_transport(clock, [(1.0, "hello")])
        chain = build_chain(_settings(ai_provider="groq", ai_timeout_s=10.0), transport=transport)
        response = chain.chat("s", "u")
        assert response.text == "hello"
        assert response.provider_id == "groq"
        # Even without an explicit deadline the attempt is bounded.
        assert calls[0]["timeout_s"] <= 10.0


class TestCaseWallTime:
    """The case must actually finish inside its configured deadline."""

    def test_case_wall_time_stays_within_the_deadline_with_retries_and_turns(self) -> None:
        """Real clock, short deadlines, a provider that blocks on every attempt.

        This is the live-failure shape: nothing ever answers. The case must end
        by itself, close to the configured budget, rather than being preempted
        by the outer watchdog after the budget has already passed.
        """
        import time as real_time

        from app.ai.deadline import Deadline as RealDeadline

        blocked_calls: list[float] = []

        def blocking_transport(
            method: str,
            url: str,
            headers: dict[str, str],
            body: bytes,
            timeout_s: float,
        ) -> tuple[int, bytes]:
            blocked_calls.append(timeout_s)
            real_time.sleep(timeout_s)
            raise LLMError("transport", "attempt timed out", retryable=True, timeout=True)

        chain = build_chain(
            _settings(
                ai_timeout_s=0.2,
                investigator_turn_timeout_s=0.6,
                investigator_timeout_s=1.2,
                investigator_safety_reserve_s=0.05,
                investigator_min_attempt_s=0.05,
                ai_provider_max_attempts=2,
            ),
            transport=blocking_transport,
        )
        deadline = RealDeadline.after(1.2, safety_reserve_s=0.05, min_attempt_s=0.05)

        started = real_time.monotonic()
        with pytest.raises(AIChainError):
            chain.chat_with_attempts("s", "u", deadline=deadline)
        elapsed = real_time.monotonic() - started

        # Bounded by the case budget plus a small tolerance, NOT by the sum of
        # every attempt cap across both providers and both retries.
        assert elapsed < 1.2 + 0.5, f"case overran its deadline: {elapsed:.3f}s"
        # Every attempt was capped, and none was given more than remained.
        assert blocked_calls
        assert max(blocked_calls) <= 0.2 + 1e-6

    def test_no_attempt_is_given_more_time_than_the_deadline_allows(self) -> None:
        clock = FakeClock()
        transport, calls = scripted_transport(
            clock, [(600.0, None), (600.0, None), (600.0, None), (600.0, None)]
        )
        chain = build_chain(
            _settings(
                ai_timeout_s=10.0,
                investigator_timeout_s=25.0,
                ai_provider_max_attempts=2,
                investigator_fallback_reserve_s=0.0,
            ),
            transport=transport,
        )
        deadline = Deadline.after(25.0, safety_reserve_s=0.5, now=clock())
        with pytest.raises(AIChainError):
            chain.chat_with_attempts("s", "u", deadline=deadline, now=clock)

        # Attempts shrink as the deadline approaches; the last one is refused.
        assert calls[0]["timeout_s"] == pytest.approx(10.0)
        assert calls[1]["timeout_s"] == pytest.approx(10.0)
        assert calls[2]["timeout_s"] == pytest.approx(4.5)
        assert clock.now - 1_000.0 <= 25.0


class TestDeadlineExhaustionIsSafe:
    def test_exhaustion_metadata_holds_no_secret_and_no_sentinel(self) -> None:
        clock = FakeClock()
        secret = "gsk_sentinel_value_that_must_never_appear"
        transport, _calls = scripted_transport(clock, [(600.0, None), (600.0, None)])
        chain = build_chain(
            _settings(
                groq_api_key=secret,
                sarvam_api_key=secret,
                ai_timeout_s=10.0,
                # Groq consumes 10s of an 11s budget, leaving too little for a
                # viable Sarvam attempt: it is refused, not started.
                investigator_timeout_s=11.0,
                ai_provider_max_attempts=1,
                investigator_fallback_reserve_s=0.0,
            ),
            transport=transport,
        )
        with pytest.raises(AIChainError) as excinfo:
            chain.chat_with_attempts(
                "s",
                "u",
                deadline=Deadline.after(11.0, safety_reserve_s=0.5, now=clock()),
                now=clock,
            )
        payload = json.dumps([item.to_json() for item in excinfo.value.attempts])
        assert secret not in payload
        assert "gsk_" not in payload
        assert secret not in str(excinfo.value)
        outcomes = [item.outcome for item in excinfo.value.attempts]
        assert "TIMEOUT" in outcomes
        assert "DEADLINE_EXHAUSTED" in outcomes


class TestFallbackReservation:
    """REVIEW-006: a first provider must not starve its fallback.

    The reproduction: with two 11-second attempts inside a 25-second turn,
    Sarvam received 1.75-2.25 seconds and could not realistically answer.

    The guarantee, stated precisely and asserted exactly: the LAST provider in
    the chain receives the full per-attempt cap, at every configured attempt
    count. Retry backoff is reservation-aware, so it cannot erode the reserve
    either - an earlier revision let two 0.5s backoffs reduce the fallback to
    10.5s while claiming a full 11s.
    """

    def test_production_defaults_let_a_realistically_slow_fallback_answer(self) -> None:
        """UNMODIFIED production defaults. Nothing is overridden here."""
        clock = FakeClock()
        # Groq consumes its entire attempt cap; Sarvam needs a realistic 3 s.
        transport, calls = scripted_transport(clock, [(600.0, None), (3.0, '{"action": "final"}')])
        settings = Settings(
            ai_provider="auto",
            groq_api_key="gsk_scripted_offline_only",
            sarvam_api_key="scripted_offline_only",
            _env_file=None,
        )
        policy = policy_from_settings(settings)
        chain = build_chain(settings, transport=transport)
        case = Deadline.after(
            policy.case_deadline_s,
            safety_reserve_s=policy.safety_reserve_s,
            now=clock(),
            min_attempt_s=policy.min_attempt_s,
        )
        turn = case.sub_deadline(policy.turn_deadline_s, now=clock())

        outcome = chain.chat_with_attempts(
            "system", "user", json_mode=True, deadline=turn, now=clock
        )

        # Groq got its full cap, and so did Sarvam. The guarantee asserted here
        # is exactly the one documented: the LAST provider receives the full
        # per-attempt cap, which comfortably covers a 3-second answer.
        assert calls[0]["timeout_s"] == pytest.approx(policy.attempt_timeout_cap_s)
        assert calls[1]["timeout_s"] == pytest.approx(policy.attempt_timeout_cap_s)
        assert outcome.response.provider_id == "sarvam"
        assert list(outcome.attempted_providers) == ["groq", "sarvam"]
        assert outcome.actual_provider == "sarvam"
        # Inside both the turn window and the case deadline.
        assert clock.now - 1_000.0 <= policy.turn_deadline_s
        assert not case.expired(now=clock())

    def test_default_is_one_attempt_per_provider(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.ai_provider_max_attempts == 1
        assert policy_from_settings(settings).max_attempts_per_provider == 1

    @pytest.mark.parametrize("attempts", [1, 2, 3])
    def test_the_reserve_protects_the_fallback_at_every_attempt_count(self, attempts: int) -> None:
        """Raising attempts stays safe: the reserve still holds a full attempt."""
        clock = FakeClock()
        transport, calls = scripted_transport(clock, [(600.0, None)] * 6)
        settings = _settings(ai_provider_max_attempts=attempts)
        policy = policy_from_settings(settings)
        chain = build_chain(settings, transport=transport)
        turn = Deadline.after(
            policy.case_deadline_s,
            safety_reserve_s=policy.safety_reserve_s,
            now=clock(),
            min_attempt_s=policy.min_attempt_s,
        ).sub_deadline(policy.turn_deadline_s, now=clock())

        with pytest.raises(AIChainError):
            chain.chat_with_attempts("s", "u", deadline=turn, now=clock)

        sarvam = [item["timeout_s"] for item in calls if "sarvam" in item["url"]]
        assert sarvam, "the fallback provider was never dialled"
        # The documented guarantee, asserted exactly: the last provider gets a
        # FULL attempt cap regardless of how many retries precede it. Retry
        # backoff is reservation-aware, so it cannot erode this.
        assert sarvam[0] == pytest.approx(policy.attempt_timeout_cap_s), (
            f"fallback received {sarvam} instead of a full {policy.attempt_timeout_cap_s}s cap"
        )

    def test_the_last_provider_reserves_nothing(self) -> None:
        """Reserving for a successor that does not exist would strand time."""
        clock = FakeClock()
        transport, calls = scripted_transport(clock, [(3.0, '{"action": "final"}')])
        settings = _settings(ai_provider="groq", sarvam_api_key=None)
        policy = policy_from_settings(settings)
        chain = build_chain(settings, transport=transport)
        assert chain.member_ids == ["groq"]
        turn = Deadline.after(
            policy.case_deadline_s,
            safety_reserve_s=policy.safety_reserve_s,
            now=clock(),
            min_attempt_s=policy.min_attempt_s,
        ).sub_deadline(policy.turn_deadline_s, now=clock())

        chain.chat_with_attempts("s", "u", deadline=turn, now=clock)
        assert calls[0]["timeout_s"] == pytest.approx(policy.attempt_timeout_cap_s)

    def test_the_reserve_is_configurable_and_clamped(self) -> None:
        explicit = policy_from_settings(_settings(investigator_fallback_reserve_s=4.0))
        assert explicit.fallback_reserve_s == pytest.approx(4.0)
        # Zero is honoured, not replaced by the default.
        zeroed = policy_from_settings(_settings(investigator_fallback_reserve_s=0.0))
        assert zeroed.fallback_reserve_s == pytest.approx(0.0)
        # A reserve larger than the turn would block the FIRST provider, so it
        # is clamped to leave a viable first attempt.
        huge = policy_from_settings(
            _settings(
                investigator_fallback_reserve_s=10_000.0,
                investigator_turn_timeout_s=25.0,
            )
        )
        assert huge.fallback_reserve_s <= 25.0 - huge.safety_reserve_s - huge.min_attempt_s


class TestConfigurationEdgeSemantics:
    """REVIEW-010: accepted values are honoured; invariants are real."""

    def test_zero_safety_reserve_is_honoured_not_silently_replaced(self) -> None:
        policy = policy_from_settings(_settings(investigator_safety_reserve_s=0.0))
        assert policy.safety_reserve_s == 0.0
        # And it reaches the arithmetic: the full remaining time is usable.
        deadline = Deadline.after(20.0, safety_reserve_s=policy.safety_reserve_s, now=0.0)
        assert deadline.attempt_timeout(30.0, now=0.0) == pytest.approx(20.0)

    def test_zero_watchdog_grace_is_honoured_not_silently_replaced(self) -> None:
        policy = policy_from_settings(_settings(investigator_watchdog_grace_s=0.0))
        assert policy.watchdog_grace_s == 0.0
        assert policy.watchdog_timeout_s == pytest.approx(policy.case_deadline_s)

    def test_min_attempt_above_the_cap_is_clamped_so_the_invariant_is_real(self) -> None:
        """An attempt shorter than the stated minimum must never start."""
        policy = policy_from_settings(_settings(ai_timeout_s=1.0, investigator_min_attempt_s=5.0))
        # The minimum cannot exceed a cap it could never reach.
        assert policy.min_attempt_s <= policy.attempt_timeout_cap_s
        deadline = Deadline.after(
            60.0,
            safety_reserve_s=policy.safety_reserve_s,
            now=0.0,
            min_attempt_s=policy.min_attempt_s,
        )
        granted = deadline.attempt_timeout(policy.attempt_timeout_cap_s, now=0.0)
        assert granted is not None
        assert granted >= policy.min_attempt_s

    def test_a_reserve_that_consumes_the_whole_turn_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="leave usable time"):
            InvestigatorExecutionPolicy(
                providers=(("groq", "m"),),
                attempt_timeout_cap_s=5.0,
                turn_deadline_s=5.0,
                case_deadline_s=60.0,
                safety_reserve_s=5.0,
                min_attempt_s=1.5,
                fallback_reserve_s=0.0,
                watchdog_grace_s=5.0,
                max_attempts_per_provider=1,
                tool_call_budget=12,
                max_schema_attempts=2,
                require_tool_call_before_final=True,
                prompt_protocol_version="p",
                tool_protocol_version="t",
                result_schema_version="r",
            )

    @pytest.mark.parametrize(
        "field,value",
        [
            ("attempt_timeout_cap_s", 30.0),
            ("turn_deadline_s", 90.0),
            ("min_attempt_s", 99.0),
        ],
    )
    def test_an_inverted_hierarchy_is_rejected_at_construction(
        self, field: str, value: float
    ) -> None:
        base = policy_from_settings(_settings()).as_kwargs()
        base[field] = value
        with pytest.raises(ValueError):
            InvestigatorExecutionPolicy(**base)

    def test_recommended_defaults_are_coherent(self) -> None:
        policy = policy_from_settings(Settings(_env_file=None))
        assert policy.attempt_timeout_cap_s <= policy.turn_deadline_s
        assert policy.turn_deadline_s <= policy.case_deadline_s
        assert policy.min_attempt_s <= policy.attempt_timeout_cap_s
        assert policy.safety_reserve_s + policy.min_attempt_s <= policy.turn_deadline_s
        assert policy.watchdog_timeout_s >= policy.case_deadline_s


class TestWatchdogGraceIsPolicyIdentity:
    """REVIEW-008: every behaviour-changing deadline affects identity."""

    def test_changing_the_grace_changes_the_fingerprint(self) -> None:
        low = policy_from_settings(_settings(investigator_watchdog_grace_s=5.0))
        high = policy_from_settings(_settings(investigator_watchdog_grace_s=99.0))
        # The effective watchdog really does change.
        assert low.watchdog_timeout_s != high.watchdog_timeout_s
        assert low.fingerprint() != high.fingerprint()
        assert "watchdog_grace_s" in low.describe()

    def test_rotating_a_key_still_does_not_change_the_fingerprint(self) -> None:
        one = policy_from_settings(_settings(groq_api_key="gsk_one")).fingerprint()
        two = policy_from_settings(_settings(groq_api_key="gsk_two")).fingerprint()
        assert one == two

    def test_the_fingerprint_material_is_versioned_and_non_secret(self) -> None:
        policy = policy_from_settings(_settings())
        assert "watchdog_grace_s" in policy.describe()
        assert "fallback_reserve_s" in policy.describe()
        payload = json.dumps(policy.describe(), sort_keys=True)
        assert "gsk_" not in payload


class TestAttemptedMeansContacted:
    """REVIEW-011: a provider never dialled must not be reported as attempted.

    The reproduction: a 10-second deadline allowed exactly one Groq transport
    call, Sarvam was never dialled, yet attempted_providers was
    ("groq", "sarvam").
    """

    def test_a_deadline_refused_fallback_is_not_reported_as_attempted(self) -> None:
        clock = FakeClock()
        contacted: list[str] = []

        def transport(
            method: str,
            url: str,
            headers: dict[str, str],
            body: bytes,
            timeout_s: float,
        ) -> tuple[int, bytes]:
            contacted.append("sarvam" if "sarvam" in url else "groq")
            clock.advance(timeout_s)
            raise LLMError("transport", "attempt timed out", retryable=True, timeout=True)

        chain = build_chain(
            _settings(
                ai_timeout_s=10.0,
                investigator_timeout_s=10.0,
                ai_provider_max_attempts=1,
                investigator_fallback_reserve_s=0.0,
            ),
            transport=transport,
        )
        with pytest.raises(AIChainError) as excinfo:
            chain.chat_with_attempts(
                "s",
                "u",
                deadline=Deadline.after(10.0, safety_reserve_s=0.5, now=clock(), min_attempt_s=1.5),
                now=clock,
            )

        failure = excinfo.value
        # The headline assertion: transport identities ARE attempted_providers.
        assert contacted == ["groq"]
        assert list(failure.attempted_providers) == contacted
        # Sarvam was reached in the walk but never dialled.
        assert list(failure.considered_providers) == ["groq", "sarvam"]
        assert list(failure.skipped_providers) == ["sarvam"]
        # Its record survives, classified as a refusal rather than a contact.
        refusals = [item for item in failure.attempts if not item.contacted]
        assert [item.provider_id for item in refusals] == ["sarvam"]
        assert [item.outcome for item in refusals] == ["DEADLINE_EXHAUSTED"]

    def test_a_contacted_provider_that_timed_out_is_reported_as_attempted(self) -> None:
        clock = FakeClock()
        transport, calls = scripted_transport(clock, [(600.0, None), (600.0, None)])
        chain = build_chain(
            _settings(ai_timeout_s=10.0, investigator_timeout_s=75.0, ai_provider_max_attempts=1),
            transport=transport,
        )
        with pytest.raises(AIChainError) as excinfo:
            chain.chat_with_attempts(
                "s",
                "u",
                deadline=Deadline.after(75.0, safety_reserve_s=0.5, now=clock(), min_attempt_s=1.5),
                now=clock,
            )
        dialled = ["sarvam" if "sarvam" in item["url"] else "groq" for item in calls]
        assert dialled == ["groq", "sarvam"]
        assert list(excinfo.value.attempted_providers) == dialled
        assert list(excinfo.value.skipped_providers) == []

    def test_a_successful_fallback_is_both_attempted_and_actual(self) -> None:
        clock = FakeClock()
        transport, calls = scripted_transport(clock, [(600.0, None), (3.0, '{"action": "final"}')])
        chain = build_chain(
            _settings(ai_timeout_s=10.0, investigator_timeout_s=75.0, ai_provider_max_attempts=1),
            transport=transport,
        )
        outcome = chain.chat_with_attempts(
            "s",
            "u",
            deadline=Deadline.after(75.0, safety_reserve_s=0.5, now=clock(), min_attempt_s=1.5),
            now=clock,
        )
        dialled = ["sarvam" if "sarvam" in item["url"] else "groq" for item in calls]
        assert list(outcome.attempted_providers) == dialled == ["groq", "sarvam"]
        assert outcome.actual_provider == "sarvam"
        assert list(outcome.skipped_providers) == []

    def test_execution_order_is_preserved_not_sorted(self) -> None:
        """Sorting would hide which provider was tried first."""
        clock = FakeClock()
        transport, _calls = scripted_transport(clock, [(600.0, None), (1.0, '{"action": "final"}')])
        # Alphabetically sarvam > groq, so a sorted list happens to match here;
        # assert the tuple identity against the real walk order instead.
        chain = build_chain(
            _settings(ai_timeout_s=10.0, investigator_timeout_s=75.0, ai_provider_max_attempts=1),
            transport=transport,
        )
        outcome = chain.chat_with_attempts(
            "s",
            "u",
            deadline=Deadline.after(75.0, safety_reserve_s=0.5, now=clock(), min_attempt_s=1.5),
            now=clock,
        )
        assert outcome.attempted_providers == ("groq", "sarvam")
        assert outcome.considered_providers == ("groq", "sarvam")

    def test_refusal_metadata_carries_no_secret_url_or_body(self) -> None:
        clock = FakeClock()
        secret = "gsk_refusal_sentinel_must_never_persist"
        transport, _calls = scripted_transport(clock, [(600.0, None)])
        chain = build_chain(
            _settings(
                groq_api_key=secret,
                sarvam_api_key=secret,
                ai_timeout_s=10.0,
                investigator_timeout_s=11.0,
                ai_provider_max_attempts=1,
                investigator_fallback_reserve_s=0.0,
            ),
            transport=transport,
        )
        with pytest.raises(AIChainError) as excinfo:
            chain.chat_with_attempts(
                "s",
                "u",
                deadline=Deadline.after(11.0, safety_reserve_s=0.5, now=clock(), min_attempt_s=1.5),
                now=clock,
            )
        payload = json.dumps([item.to_json() for item in excinfo.value.attempts])
        assert secret not in payload
        assert "gsk_" not in payload
        for banned in ("Authorization", "Bearer", "https://", "chat/completions"):
            assert banned not in payload
        assert "DEADLINE_EXHAUSTED" in payload


class TestEffectivePolicyReachesTheRuntime:
    """REVIEW-012: the configured minimum must reach the real deadline.

    The reproduction: policy reported min_attempt_s=0.1 with a 0.2s cap, but
    the live deadline used Deadline's 1.5s module default, so a provider able
    to answer in 0.15s received ZERO transport calls.
    """

    def test_a_small_configured_minimum_reaches_the_transport(self) -> None:
        """Settings -> policy -> selection -> investigate -> transport."""
        granted: list[float] = []

        def transport(
            method: str,
            url: str,
            headers: dict[str, str],
            body: bytes,
            timeout_s: float,
        ) -> tuple[int, bytes]:
            granted.append(timeout_s)
            # A response well inside the 0.2s cap and above the 0.1s minimum.
            turn = json.dumps(
                {
                    "action": "final",
                    "unresolved": {
                        "reason_codes": ["NON_UNIQUE_EVIDENCE"],
                        "missing_evidence": ["x"],
                        "next_step": "review",
                    },
                }
            )
            return 200, json.dumps({"choices": [{"message": {"content": turn}}]}).encode("utf-8")

        settings = Settings(
            ai_provider="groq",
            groq_api_key="gsk_scripted_offline_only",
            ai_timeout_s=0.2,
            investigator_turn_timeout_s=0.5,
            investigator_timeout_s=1.0,
            investigator_safety_reserve_s=0.0,
            investigator_min_attempt_s=0.1,
            _env_file=None,
        )
        policy = policy_from_settings(settings)
        assert policy.min_attempt_s == pytest.approx(0.1)
        assert policy.attempt_timeout_cap_s == pytest.approx(0.2)

        selection = resolve_investigator(settings, "agent", transport=transport)
        # The chain received the EFFECTIVE policy, not raw settings.
        chain = selection.provider.chain
        assert chain.min_attempt_s == pytest.approx(policy.min_attempt_s)
        assert chain.attempt_timeout_cap_s == pytest.approx(policy.attempt_timeout_cap_s)
        assert chain.safety_reserve_s == pytest.approx(policy.safety_reserve_s)
        assert chain.fallback_reserve_s == pytest.approx(policy.fallback_reserve_s)
        assert chain.max_attempts_per_provider == policy.max_attempts_per_provider

        records, cases = _make_duplicate_ledger_fixtures()
        summary = investigate_cases(records, cases, selection.provider).summary()

        # The provider was actually dialled, at the configured cap.
        assert granted, "no transport call was made under the configured minimum"
        assert granted[0] == pytest.approx(0.2)
        assert summary["attempted_providers"] == ["groq"]
        assert summary["actual_providers"] == ["groq"]

    def test_a_configured_zero_safety_reserve_reaches_the_transport(self) -> None:
        granted: list[float] = []

        def transport(
            method: str,
            url: str,
            headers: dict[str, str],
            body: bytes,
            timeout_s: float,
        ) -> tuple[int, bytes]:
            granted.append(timeout_s)
            raise LLMError("transport", "attempt timed out", retryable=True, timeout=True)

        settings = Settings(
            ai_provider="groq",
            groq_api_key="gsk_scripted_offline_only",
            ai_timeout_s=4.0,
            investigator_turn_timeout_s=4.0,
            investigator_timeout_s=4.0,
            investigator_safety_reserve_s=0.0,
            ai_provider_max_attempts=1,
            _env_file=None,
        )
        policy = policy_from_settings(settings)
        assert policy.safety_reserve_s == 0.0
        chain = build_chain(settings, transport=transport, policy=policy)
        with pytest.raises(AIChainError):
            chain.chat_with_attempts(
                "s",
                "u",
                deadline=Deadline.after(
                    4.0, safety_reserve_s=0.0, now=1_000.0, min_attempt_s=policy.min_attempt_s
                ),
                now=lambda: 1_000.0,
            )
        # Zero reserve means the whole window is usable.
        assert granted[0] == pytest.approx(4.0)

    def test_a_clamped_minimum_reaches_the_real_case_deadline(self) -> None:
        """min_attempt_s above the cap is clamped, and the clamp propagates."""
        settings = Settings(
            ai_provider="groq",
            groq_api_key="gsk_scripted_offline_only",
            ai_timeout_s=2.0,
            investigator_min_attempt_s=9.0,
            _env_file=None,
        )
        policy = policy_from_settings(settings)
        assert policy.min_attempt_s == pytest.approx(2.0)
        selection = resolve_investigator(settings, "agent")
        assert selection.provider.chain.min_attempt_s == pytest.approx(2.0)
        assert selection.policy is not None
        assert selection.policy.min_attempt_s == pytest.approx(2.0)

    def test_an_inverted_configuration_stays_clamped_through_selection(self) -> None:
        settings = Settings(
            ai_provider="groq",
            groq_api_key="gsk_scripted_offline_only",
            ai_timeout_s=45.0,
            investigator_turn_timeout_s=90.0,
            investigator_timeout_s=30.0,
            _env_file=None,
        )
        chain = resolve_investigator(settings, "agent").provider.chain
        assert chain.attempt_timeout_cap_s <= 30.0
        policy = policy_from_settings(settings)
        assert policy.attempt_timeout_cap_s <= policy.turn_deadline_s <= policy.case_deadline_s

    def test_the_recommended_defaults_survive_the_whole_path(self) -> None:
        settings = Settings(
            ai_provider="groq", groq_api_key="gsk_scripted_offline_only", _env_file=None
        )
        policy = policy_from_settings(settings)
        assert policy.attempt_timeout_cap_s == pytest.approx(11.0)
        assert policy.turn_deadline_s == pytest.approx(25.0)
        assert policy.case_deadline_s == pytest.approx(75.0)
        chain = resolve_investigator(settings, "agent").provider.chain
        assert chain.attempt_timeout_cap_s == pytest.approx(11.0)
        assert chain.min_attempt_s == pytest.approx(policy.min_attempt_s)
