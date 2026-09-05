"""Mandatory §19 guarantees: honest providers, evidence use, and identity.

Every test here is network-free and uses either a scripted chain or the
deterministic fake. None needs a real key.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.ai.base import LLMResponse
from app.ai.chain import AIChain
from app.ai.policy import policy_from_settings
from app.ai.selection import InvestigatorUnavailableError, resolve_investigator
from app.config import Settings
from app.domain.enums import CaseStatus, ExceptionCategory
from app.investigator.budgets import InvestigationBudget
from app.investigator.engine import investigate_cases
from app.investigator.failures import InvestigatorExecutionError
from app.investigator.llm_provider import LLMInvestigatorProvider
from app.investigator.provider import FakeProvider
from app.persistence.database import Database
from app.reconciliation.detectors import CaseEvidence, CaseRecord
from app.runs import AgentProviderRequiredError, execute_run
from app.workflow.controller import request_key_for

DEV_INPUTS = Path("datasets/dev/inputs")


def _chain(turns: list[str], provider_id: str = "scripted-groq") -> AIChain:
    queue = list(turns)

    class Backend:
        provider_id = "scripted-groq"
        model = "scripted-model-1"

        def chat(
            self,
            system: str,
            user: str,
            json_mode: bool = False,
            timeout_s: float | None = None,
        ) -> LLMResponse:
            return LLMResponse(
                text=queue.pop(0),
                provider_id=self.provider_id,
                model=self.model,
                latency_ms=0.0,
            )

    backend = Backend()
    backend.provider_id = provider_id
    return AIChain([backend])


def _case() -> CaseRecord:
    return CaseRecord(
        case_id="case-abc123def456",
        category=ExceptionCategory.AMBIGUOUS_EVIDENCE,
        status=CaseStatus.OPEN,
        variance_paise=2116738,
        affected_amount_paise=2116738,
        proposed_delta_paise=None,
        currency="INR",
        summary="ambiguous evidence",
        reason_codes=(),
        evidence=(CaseEvidence(record_type="LEDGER_ENTRY", record_id="led_4w1kiapkxU"),),
    )


class TestAgentModeRequiresAnExplicitProvider:
    """§19.8 - the core must never substitute the fake for a requested AI run."""

    def test_direct_execute_run_agent_without_provider_fails_explicitly(
        self, tmp_path: Path
    ) -> None:
        db = Database(tmp_path / "explicit.sqlite3")
        try:
            with pytest.raises(AgentProviderRequiredError) as excinfo:
                execute_run(DEV_INPUTS, db, mode="agent")
            assert "explicit investigator provider" in str(excinfo.value)
            # Nothing was persisted, and no fake identity was invented.
            assert db.query_all("SELECT run_id FROM runs") == []
        finally:
            db.close()

    def test_rules_only_still_needs_no_provider(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "rules.sqlite3")
        try:
            result = execute_run(DEV_INPUTS, db, mode="rules-only")
            assert result.summary["mode"] == "rules-only"
            assert result.summary["provider_id"] == "none"
            assert result.summary["investigation_status"] == "NOT_INVESTIGATED"
        finally:
            db.close()

    def test_explicit_fake_selection_still_works_and_is_labelled(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "fake.sqlite3")
        try:
            result = execute_run(DEV_INPUTS, db, mode="agent", provider=FakeProvider())
            assert result.summary["provider_id"] == "fake-deterministic-v1"
            assert "investigation" in result.summary
        finally:
            db.close()

    def test_a_live_request_without_a_key_is_an_error_not_a_fake(self) -> None:
        settings = Settings(ai_provider="auto", _env_file=None)
        with pytest.raises(InvestigatorUnavailableError):
            resolve_investigator(settings, "agent")


class TestMandatoryEvidenceToolCall:
    """§19.7 - a live final answer requires real evidence use."""

    def _tools(self) -> Any:
        from tests.unit.test_llm_provider import _dispatcher_for

        return _dispatcher_for(_case())

    def test_zero_tool_final_is_rejected_within_the_retry_budget(self) -> None:
        final = json.dumps(
            {
                "action": "final",
                "unresolved": {
                    "reason_codes": ["NON_UNIQUE_EVIDENCE"],
                    "missing_evidence": ["unique UTR"],
                    "next_step": "manual review",
                },
            }
        )
        # Two zero-tool finals in a row exhaust the schema-retry budget.
        provider = LLMInvestigatorProvider(_chain([final, final]))
        with pytest.raises(InvestigatorExecutionError) as excinfo:
            provider.investigate(
                _case(),
                self._tools(),
                InvestigationBudget(max_total_attempts=2, remaining_attempts=2),
                {},
            )
        assert excinfo.value.code == "FINAL_WITHOUT_CASE_EVIDENCE"
        assert excinfo.value.evidence_tool_calls == 0

    def test_a_tool_call_then_final_is_accepted(self) -> None:
        turns = [
            json.dumps(
                {
                    "action": "tool",
                    "tool": "get_case",
                    "arguments": {"case_id": "case-abc123def456"},
                }
            ),
            json.dumps(
                {
                    "action": "final",
                    "unresolved": {
                        "reason_codes": ["NON_UNIQUE_EVIDENCE"],
                        "missing_evidence": ["unique UTR"],
                        "next_step": "manual review",
                    },
                }
            ),
        ]
        provider = LLMInvestigatorProvider(_chain(turns))
        result = provider.investigate(_case(), self._tools(), InvestigationBudget(), {})
        assert result.unresolved is not None
        assert result.tool_calls_used == 1
        assert result.attempts
        assert result.attempts[0]["provider_id"] == "scripted-groq"
        assert result.attempts[0]["outcome"] == "SUCCESS"

    def test_a_forbidden_tool_does_not_count_as_evidence_use(self) -> None:
        """A rejected dispatch is not evidence, so the final stays refused."""
        turns = [
            json.dumps(
                {
                    "action": "tool",
                    "tool": "approve_correction",
                    "arguments": {"case_id": "case-abc123def456"},
                }
            ),
            json.dumps(
                {
                    "action": "final",
                    "unresolved": {
                        "reason_codes": ["NON_UNIQUE_EVIDENCE"],
                        "missing_evidence": ["x"],
                        "next_step": "manual review",
                    },
                }
            ),
            json.dumps(
                {
                    "action": "final",
                    "unresolved": {
                        "reason_codes": ["NON_UNIQUE_EVIDENCE"],
                        "missing_evidence": ["x"],
                        "next_step": "manual review",
                    },
                }
            ),
        ]
        provider = LLMInvestigatorProvider(_chain(turns))
        with pytest.raises(InvestigatorExecutionError) as excinfo:
            provider.investigate(
                _case(),
                self._tools(),
                InvestigationBudget(max_total_attempts=2, remaining_attempts=2),
                {},
            )
        assert excinfo.value.code == "FINAL_WITHOUT_CASE_EVIDENCE"
        assert excinfo.value.evidence_tool_calls == 0

    def test_the_fake_provider_is_not_subject_to_the_live_rule(self) -> None:
        """Explicit deterministic evaluation stays available and unchanged."""
        assert not hasattr(FakeProvider(), "policy")
        assert FakeProvider().policy_fingerprint == "fake-deterministic-v1-policy"


class TestInvestigationFailureIsFinanciallySafe:
    """§19.10 - a failed investigation never closes or corrects a case."""

    def test_a_deadline_failure_leaves_the_case_unresolved_with_no_proof(self) -> None:
        from tests.unit.test_investigator_engine import (  # type: ignore[attr-defined]
            _make_duplicate_ledger_fixtures,
        )

        records, cases = _make_duplicate_ledger_fixtures()

        class DeadProvider:
            provider_id = "llm:scripted-dead"
            policy_fingerprint = "policy-test"

            def investigate(self, case: Any, tools: Any, budget: Any, context: Any) -> Any:
                raise TimeoutError("no provider completed a turn")

        outcome = investigate_cases(records, cases, DeadProvider())
        summary = outcome.summary()

        assert summary["investigation_failure_count"] == 1
        assert summary["fully_investigated"] is False
        # No provider answered, so the strong claim is empty.
        assert summary["actual_providers"] == []
        for item in outcome.investigations:
            assert item.status == "FAILED"
            assert item.case.status == CaseStatus.INVESTIGATION_FAILED
            assert item.proof is None
            assert item.dry_run is None
            assert item.hypothesis is None
            assert item.verifier_result is None

    def test_a_run_with_investigation_failures_is_labelled_not_fully_investigated(
        self, tmp_path: Path
    ) -> None:
        db = Database(tmp_path / "warned.sqlite3")

        class DeadProvider:
            provider_id = "llm:scripted-dead"
            policy_fingerprint = "policy-test"

            def investigate(self, case: Any, tools: Any, budget: Any, context: Any) -> Any:
                raise TimeoutError("no provider completed a turn")

        try:
            result = execute_run(DEV_INPUTS, db, mode="agent", provider=DeadProvider())
            # The financial run still completes safely...
            assert result.summary["batch_status"] == "COMPLETED"
            # ...but is never presented as fully investigated.
            assert result.summary["investigation_status"] == (
                "COMPLETED_WITH_INVESTIGATION_FAILURES"
            )
            assert result.summary["investigation_failure_count"] > 0
            assert "remain unresolved" in result.summary["investigation_status_detail"]
        finally:
            db.close()

    def test_a_clean_fake_run_is_labelled_fully_investigated(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "clean.sqlite3")
        try:
            result = execute_run(DEV_INPUTS, db, mode="agent", provider=FakeProvider())
            assert result.summary["investigation_status"] in {
                "FULLY_INVESTIGATED",
                "NO_CASES_REQUIRED_INVESTIGATION",
            }
            assert result.summary["investigation_failure_count"] == 0
        finally:
            db.close()


class TestPolicyIsPartOfIdempotency:
    """§19.9 - a corrected policy must not reuse a timed-out result."""

    def test_a_changed_policy_produces_a_distinct_run(self, tmp_path: Path) -> None:
        class Provider(FakeProvider):
            def __init__(self, fingerprint: str) -> None:
                self._fingerprint = fingerprint

            @property
            def policy_fingerprint(self) -> str:
                return self._fingerprint

        db = Database(tmp_path / "idem.sqlite3")
        try:
            first = execute_run(DEV_INPUTS, db, mode="agent", provider=Provider("policy-old"))
            repeat = execute_run(DEV_INPUTS, db, mode="agent", provider=Provider("policy-old"))
            corrected = execute_run(DEV_INPUTS, db, mode="agent", provider=Provider("policy-new"))

            # Identical policy: exactly one economic result, reused.
            assert first.reused is False
            assert repeat.reused is True
            assert repeat.run_id == first.run_id
            # Corrected policy: a new attempt, not the old timed-out result.
            assert corrected.reused is False
            assert corrected.run_id != first.run_id
            assert corrected.idempotency_key != first.idempotency_key
        finally:
            db.close()

    def test_a_changed_policy_produces_a_distinct_job_key(self) -> None:
        common = {
            "session_id": "session-a",
            "snapshot_identity": "snapshot-1",
            "requested_mode": "agent",
            "provider_id": "llm:groq",
        }
        old = request_key_for(**common, policy_fingerprint="policy-old")
        same = request_key_for(**common, policy_fingerprint="policy-old")
        new = request_key_for(**common, policy_fingerprint="policy-new")
        assert old == same
        assert old != new

    def test_the_model_id_reaches_the_fingerprint(self) -> None:
        def fingerprint(model: str) -> str:
            return policy_from_settings(
                Settings(
                    ai_provider="groq",
                    groq_api_key="synthetic_offline",
                    groq_investigator_model=model,
                    _env_file=None,
                )
            ).fingerprint()

        assert fingerprint("openai/gpt-oss-20b") != fingerprint("openai/gpt-oss-120b")


class TestWatchdogGraceReachesRunAndJobIdentity:
    """REVIEW-008: a behaviour-changing deadline must change both identities."""

    @staticmethod
    def _policy(grace: float) -> Any:
        return policy_from_settings(
            Settings(
                ai_provider="groq",
                groq_api_key="synthetic_offline_only",
                investigator_watchdog_grace_s=grace,
                _env_file=None,
            )
        )

    def test_changing_the_grace_changes_the_agent_run_identity(self) -> None:
        from app.runs import compute_idempotency_key

        low = self._policy(5.0)
        high = self._policy(99.0)
        assert low.fingerprint() != high.fingerprint()

        def key(fingerprint: str) -> str:
            return compute_idempotency_key(
                "fingerprint-of-inputs",
                mode="agent",
                provider_id="llm:groq",
                policy_fingerprint=fingerprint,
            )

        assert key(low.fingerprint()) != key(high.fingerprint())
        # Same policy, same run identity.
        assert key(low.fingerprint()) == key(self._policy(5.0).fingerprint())

    def test_changing_the_grace_changes_the_reconciliation_job_identity(self) -> None:
        common = {
            "session_id": "session-a",
            "snapshot_identity": "snapshot-1",
            "requested_mode": "agent",
            "provider_id": "llm:groq",
        }
        low = request_key_for(**common, policy_fingerprint=self._policy(5.0).fingerprint())
        high = request_key_for(**common, policy_fingerprint=self._policy(99.0).fingerprint())
        assert low != high

    def test_a_queued_job_is_refused_under_a_changed_policy(self, tmp_path: Path) -> None:
        """An old queued job must never execute under a new identity."""
        from app.workflow.controller import ReconciliationController

        settings = Settings(
            db_path=tmp_path / "queued.sqlite3",
            import_staging_root=tmp_path / "imports",
            ai_provider="fake",
            _env_file=None,
        )
        database = Database(settings.db_path)
        snapshot = tmp_path / "snapshot"
        snapshot.mkdir()
        try:
            controller = ReconciliationController(
                database, settings, run_executor=lambda **_: None, background=False
            )
            job, _ = controller.create_job(
                session_id="session-a",
                snapshot_path=snapshot,
                snapshot_manifest={"active_sources": {}},
                requested_mode="fake",
                execution_mode="agent",
                provider_id="fake-deterministic-v1",
                simulated=True,
                # Persisted under a policy that no longer matches the resolver.
                policy_fingerprint="policy-from-an-older-release",
            )
            finished = controller.run_once(job["job_id"])
            assert finished["status"] == "FAILED"
            assert finished["failure_code"] == "PROVIDER_UNAVAILABLE"
            assert "policy" in finished["failure_detail"].lower()
        finally:
            database.close()

    def test_rotating_a_key_changes_neither_identity(self) -> None:
        def fingerprint(key_value: str) -> str:
            return policy_from_settings(
                Settings(ai_provider="groq", groq_api_key=key_value, _env_file=None)
            ).fingerprint()

        assert fingerprint("synthetic_one") == fingerprint("synthetic_two")
        common = {
            "session_id": "session-a",
            "snapshot_identity": "snapshot-1",
            "requested_mode": "agent",
            "provider_id": "llm:groq",
        }
        assert request_key_for(
            **common, policy_fingerprint=fingerprint("synthetic_one")
        ) == request_key_for(**common, policy_fingerprint=fingerprint("synthetic_two"))
