"""Safety regressions for the Chunk 2 live Groq acceptance runner.

Every test here is OFFLINE. None constructs a provider chain, none reads the
owner's key, and none contacts Groq or any other host: the runner's pure
helpers are exercised directly with sentinel values.

The script is imported as a standalone module, the same way
``test_verify_phase_safety.py`` imports ``scripts/verify_phase.py``.

Each test corresponds to a finding reproduced against the pre-hardening runner
and recorded in cloud-reference section 34.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER_PATH = REPO_ROOT / "scripts" / "live_groq_acceptance.py"

# A sentinel that matches the runner's key-shaped pattern but is not a key.
SENTINEL_KEY = "gsk_" + "S" * 40


@pytest.fixture(scope="module")
def runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("argus_live_groq_acceptance", RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _passing_tool_safety(runner: ModuleType) -> dict[str, Any]:
    return {
        "probe_executed": True,
        "allowlist": ["get_case"],
        "allowlist_size": 1,
        "banned_in_allowlist": [],
        "write_shaped_allowlist_names": [],
        "forbidden_probed": list(runner.FORBIDDEN_TOOL_NAMES),
        "accepted_forbidden_tools": [],
        "forbidden_all_refused": True,
        "refusal_code": ["UNKNOWN_TOOL"],
    }


def _investigation(
    *,
    verifier_status: str | None,
    case_status: str,
    engine_status: str = "UNRESOLVED",
    proof: object | None = None,
    dry_run: object | None = None,
) -> SimpleNamespace:
    verifier = (
        None
        if verifier_status is None
        else SimpleNamespace(status=SimpleNamespace(value=verifier_status))
    )
    return SimpleNamespace(
        verifier_result=verifier,
        case=SimpleNamespace(status=SimpleNamespace(value=case_status)),
        proof=proof,
        dry_run=dry_run,
        status=engine_status,
    )


def _judge(
    runner: ModuleType,
    investigation: SimpleNamespace,
    tool_safety: Any,
) -> dict[str, Any]:
    settings = SimpleNamespace(groq_investigator_model="openai/gpt-oss-20b")
    policy = SimpleNamespace(watchdog_timeout_s=80.0)
    return runner.judge({"cases": [{}]}, investigation, settings, policy, 1.0, tool_safety)


# ---------------------------------------------------------------------------
# Finding 1 - secret-safe report persistence
# ---------------------------------------------------------------------------


def test_sentinel_secret_aborts_before_any_report_file_is_written(
    runner: ModuleType, tmp_path: Path
) -> None:
    """A poisoned report must leave NO file behind, not a rewritten one."""
    output = tmp_path / "acceptance-report.json"
    database = tmp_path / "acceptance.sqlite3"
    database.write_bytes(b"clean database bytes")
    console = runner.SafeConsole(SENTINEL_KEY, stream=io.StringIO())

    with pytest.raises(runner.SecretLeakError) as caught:
        runner.persist_report(
            {"leaked": f"Bearer {SENTINEL_KEY}"},
            needle=SENTINEL_KEY,
            database_path=database,
            console=console,
            output=output,
        )

    assert not output.exists(), "a report was written despite a failed leak scan"
    assert list(tmp_path.glob("acceptance-report*")) == []
    assert SENTINEL_KEY not in str(caught.value), "the exception carried key material"


def test_poisoned_database_bytes_abort_persistence(runner: ModuleType, tmp_path: Path) -> None:
    """The scan covers the temporary database, not only the report."""
    output = tmp_path / "acceptance-report.json"
    database = tmp_path / "acceptance.sqlite3"
    database.write_bytes(f"leaked {SENTINEL_KEY}".encode())
    console = runner.SafeConsole(SENTINEL_KEY, stream=io.StringIO())

    with pytest.raises(runner.SecretLeakError):
        runner.persist_report(
            {"clean": True},
            needle=SENTINEL_KEY,
            database_path=database,
            console=console,
            output=output,
        )
    assert not output.exists()


def test_clean_report_is_written_exactly_once_with_scan_metadata(
    runner: ModuleType, tmp_path: Path
) -> None:
    output = tmp_path / "acceptance-report.json"
    database = tmp_path / "acceptance.sqlite3"
    database.write_bytes(b"clean database bytes")
    console = runner.SafeConsole(SENTINEL_KEY, stream=io.StringIO())
    console.emit("a clean console line")

    final = runner.persist_report(
        {"outcome": "PREFLIGHT_ONLY"},
        needle=SENTINEL_KEY,
        database_path=database,
        console=console,
        output=output,
    )

    written = sorted(p.name for p in tmp_path.iterdir() if p.is_file())
    assert written == ["acceptance-report.json", "acceptance.sqlite3"], written
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert persisted == final
    assert persisted["leak_scan"]["clean"] is True
    assert persisted["leak_scan"]["findings"] == []
    assert persisted["leak_scan"]["final_bytes_rescanned"] is True
    # The console line and the database were both in scope.
    assert "console[0]" in persisted["leak_scan"]["scanned"]
    assert "acceptance.sqlite3" in persisted["leak_scan"]["scanned"]
    assert SENTINEL_KEY not in output.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Finding 2 - console-output guarantee
# ---------------------------------------------------------------------------


def test_console_rejects_exact_key_before_printing(runner: ModuleType, tmp_path: Path) -> None:
    sink = tmp_path / "console.txt"
    with sink.open("w", encoding="utf-8") as handle:
        console = runner.SafeConsole(SENTINEL_KEY, stream=handle)
        with pytest.raises(runner.SecretLeakError):
            console.emit(f"authorization: Bearer {SENTINEL_KEY}")
    assert sink.read_text(encoding="utf-8") == "", "the message reached the stream"
    assert console.messages == [runner.REJECTED_MESSAGE_PLACEHOLDER]
    assert SENTINEL_KEY not in "".join(console.messages)


def test_console_rejects_key_shaped_token_even_without_the_configured_key(
    runner: ModuleType, tmp_path: Path
) -> None:
    """A different provider's key shape is refused too, with no needle set."""
    sink = tmp_path / "console.txt"
    with sink.open("w", encoding="utf-8") as handle:
        console = runner.SafeConsole("", stream=handle)
        with pytest.raises(runner.SecretLeakError):
            console.emit("sk-" + "A" * 32)
    assert sink.read_text(encoding="utf-8") == ""


def test_console_records_exactly_what_it_printed(runner: ModuleType, tmp_path: Path) -> None:
    sink = tmp_path / "console.txt"
    with sink.open("w", encoding="utf-8") as handle:
        console = runner.SafeConsole(SENTINEL_KEY, stream=handle)
        console.emit("first line")
        console.emit("second line")
    assert console.messages == ["first line", "second line"]
    assert sink.read_text(encoding="utf-8").splitlines() == ["first line", "second line"]


# ---------------------------------------------------------------------------
# Finding 3 - authority-tool evidence
# ---------------------------------------------------------------------------


def test_real_probe_result_controls_the_authority_verdict(runner: ModuleType) -> None:
    investigation = _investigation(verifier_status="PASS", case_status="VERIFIED_RESOLVED")
    passing = _judge(runner, investigation, _passing_tool_safety(runner))
    assert passing["checks"]["no_authority_tool_available"] is True

    accepted = _passing_tool_safety(runner)
    accepted["accepted_forbidden_tools"] = ["approve"]
    accepted["forbidden_all_refused"] = False
    failing = _judge(runner, investigation, accepted)
    assert failing["checks"]["no_authority_tool_available"] is False
    assert failing["live_provider_acceptance"] == "FAIL"
    assert failing["chunk2_live_gate"] == "NOT YET CLOSED"


@pytest.mark.parametrize(
    "mutation",
    [
        {"probe_executed": False},
        {"forbidden_probed": ["approve"]},
        {"banned_in_allowlist": ["approve"]},
        {"write_shaped_allowlist_names": ["apply_correction"]},
    ],
)
def test_incomplete_authority_probe_cannot_pass(
    runner: ModuleType, mutation: dict[str, Any]
) -> None:
    tool_safety = _passing_tool_safety(runner)
    tool_safety.update(mutation)
    assert runner.authority_probe_passed(tool_safety) is False


def test_missing_authority_probe_cannot_pass(runner: ModuleType) -> None:
    """A skipped probe is a failure, never an assumed pass."""
    assert runner.authority_probe_passed(None) is False
    assert runner.authority_probe_passed({}) is False
    investigation = _investigation(verifier_status="PASS", case_status="VERIFIED_RESOLVED")
    verdict = _judge(runner, investigation, None)
    assert verdict["checks"]["no_authority_tool_available"] is False


def test_probe_reports_a_dispatcher_that_accepts_a_forbidden_tool(runner: ModuleType) -> None:
    class PermissiveDispatcher:
        def dispatch(self, name: str, _arguments: dict[str, Any]) -> dict[str, Any]:
            if name == "approve":
                return {"ok": True}
            return {"error": "UNKNOWN_TOOL"}

    result = runner.probe_authority_tools(PermissiveDispatcher())
    assert result["accepted_forbidden_tools"] == ["approve"]
    assert result["forbidden_all_refused"] is False
    assert runner.authority_probe_passed(result) is False


def test_probe_treats_a_raising_dispatcher_as_non_refusal(runner: ModuleType) -> None:
    class RaisingDispatcher:
        def dispatch(self, name: str, _arguments: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("dispatcher exploded")

    result = runner.probe_authority_tools(RaisingDispatcher())
    assert sorted(result["accepted_forbidden_tools"]) == sorted(runner.FORBIDDEN_TOOL_NAMES)
    assert runner.authority_probe_passed(result) is False


def test_real_dispatcher_refuses_every_forbidden_tool(runner: ModuleType) -> None:
    """The production allowlist itself exposes no authority/write tool."""

    class EmptyDispatcher:
        def dispatch(self, name: str, _arguments: dict[str, Any]) -> dict[str, Any]:
            if name in runner.TOOL_ALLOWLIST:
                return {"ok": True}
            return {"error": "UNKNOWN_TOOL"}

    result = runner.probe_authority_tools(EmptyDispatcher())
    assert result["banned_in_allowlist"] == []
    assert result["write_shaped_allowlist_names"] == []
    assert runner.authority_probe_passed(result) is True


# ---------------------------------------------------------------------------
# Finding 4 - exact Groq endpoint validation
# ---------------------------------------------------------------------------


def test_canonical_groq_endpoint_is_accepted(runner: ModuleType) -> None:
    assert runner.validate_groq_endpoint("https://api.groq.com/openai/v1") == "/openai/v1"


@pytest.mark.parametrize(
    "hostile",
    [
        # Hostname-prefix impersonation.
        "https://api.groq.com.evil.example/openai/v1",
        "https://api.groq.com.evil.example/",
        # Suffix/lookalike hosts.
        "https://evil-api.groq.com.attacker.test/openai/v1",
        "https://apixgroq.com/openai/v1",
        # Embedded credentials.
        "https://user:pass@api.groq.com/openai/v1",
        "https://api.groq.com:pass@evil.example/openai/v1",
        # Explicit port.
        "https://api.groq.com:8443/openai/v1",
        # Downgraded scheme.
        "http://api.groq.com/openai/v1",
        # Wrong or traversing base path - accepted by prefix matching.
        "https://api.groq.com/evil/v1",
        "https://api.groq.com/@evil.example/openai/v1",
        "https://api.groq.com/openai/v1/../../exfiltrate",
        "https://api.groq.com/",
        # Query and fragment smuggling.
        "https://api.groq.com/openai/v1?to=evil.example",
        "https://api.groq.com/openai/v1#evil",
    ],
)
def test_hostile_groq_endpoints_are_rejected(runner: ModuleType, hostile: str) -> None:
    with pytest.raises(runner.AcceptanceError):
        runner.validate_groq_endpoint(hostile)


def test_assert_groq_only_rejects_a_hostile_host(runner: ModuleType) -> None:
    member = SimpleNamespace(
        base_url="https://api.groq.com.evil.example/openai/v1",
        transport=runner.urllib_transport,
        model="openai/gpt-oss-20b",
        provider_id="groq",
    )
    provider = SimpleNamespace(
        chain=SimpleNamespace(member_ids=["groq"], members=[member]),
        provider_id="llm:groq",
        policy_fingerprint="fingerprint",
        policy=None,
    )
    settings = SimpleNamespace(groq_investigator_model="openai/gpt-oss-20b")
    with pytest.raises(runner.AcceptanceError):
        runner.assert_groq_only(provider, settings)


def test_assert_groq_only_rejects_a_non_production_transport(runner: ModuleType) -> None:
    def scripted(*_args: Any, **_kwargs: Any) -> tuple[int, bytes]:
        return 200, b"{}"

    member = SimpleNamespace(
        base_url="https://api.groq.com/openai/v1",
        transport=scripted,
        model="openai/gpt-oss-20b",
        provider_id="groq",
    )
    provider = SimpleNamespace(
        chain=SimpleNamespace(member_ids=["groq"], members=[member]),
        provider_id="llm:groq",
        policy_fingerprint="fingerprint",
        policy=None,
    )
    settings = SimpleNamespace(groq_investigator_model="openai/gpt-oss-20b")
    with pytest.raises(runner.AcceptanceError):
        runner.assert_groq_only(provider, settings)


# ---------------------------------------------------------------------------
# Finding 5 - verifier / dry-run / resolution invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verifier_status", ["FAIL", "INCONCLUSIVE", None])
def test_dry_run_without_verifier_pass_fails(
    runner: ModuleType, verifier_status: str | None
) -> None:
    investigation = _investigation(
        verifier_status=verifier_status,
        case_status="UNRESOLVED",
        dry_run=object(),
    )
    checks = _judge(runner, investigation, _passing_tool_safety(runner))["checks"]
    assert checks["no_dry_run_without_verifier_pass"] is False


@pytest.mark.parametrize(
    "case_status", ["VERIFIED_RESOLVED", "APPROVAL_REQUIRED", "SIMULATED_APPLIED"]
)
def test_resolved_case_status_without_verifier_pass_fails(
    runner: ModuleType, case_status: str
) -> None:
    investigation = _investigation(
        verifier_status="INCONCLUSIVE",
        case_status=case_status,
        engine_status="RESOLVED",
        proof=object(),
    )
    verdict = _judge(runner, investigation, _passing_tool_safety(runner))
    assert verdict["checks"]["no_resolution_without_verifier_pass"] is False
    assert verdict["checks"]["ambiguity_left_unresolved"] is False
    assert verdict["live_provider_acceptance"] == "FAIL"


def test_engine_resolved_without_verifier_pass_fails(runner: ModuleType) -> None:
    investigation = _investigation(
        verifier_status=None,
        case_status="UNRESOLVED",
        engine_status="RESOLVED",
    )
    checks = _judge(runner, investigation, _passing_tool_safety(runner))["checks"]
    assert checks["no_resolution_without_verifier_pass"] is False


def test_proof_without_dry_run_remains_legal(runner: ModuleType) -> None:
    """The engine builds a proof for FAIL and INCONCLUSIVE; that is the contract."""
    investigation = _investigation(
        verifier_status="INCONCLUSIVE",
        case_status="UNRESOLVED",
        engine_status="UNRESOLVED",
        proof=object(),
        dry_run=None,
    )
    checks = _judge(runner, investigation, _passing_tool_safety(runner))["checks"]
    assert checks["no_dry_run_without_verifier_pass"] is True
    assert checks["no_resolution_without_verifier_pass"] is True
    assert checks["ambiguity_left_unresolved"] is True


def test_ambiguous_unresolved_path_stays_valid(runner: ModuleType) -> None:
    """A model answering 'unresolved' is legitimate, not a safety failure."""
    investigation = _investigation(
        verifier_status=None,
        case_status="UNRESOLVED",
        engine_status="UNRESOLVED",
    )
    verdict = _judge(runner, investigation, _passing_tool_safety(runner))
    checks = verdict["checks"]
    assert checks["ambiguity_left_unresolved"] is True
    assert checks["no_dry_run_without_verifier_pass"] is True
    assert checks["no_resolution_without_verifier_pass"] is True
    assert verdict["verifier_path_observation"] == "NOT OBSERVED"
    assert verdict["chunk2_live_gate"] == "NOT YET CLOSED"


def test_provider_successes_followed_by_failure_cannot_pass(
    runner: ModuleType,
) -> None:
    """Tool turns followed by a provider failure are partial, not accepted."""
    summary = {
        "provider_id": "llm:groq",
        "actual_providers": ["groq"],
        "cases": [
            {
                "evidence_tool_calls": 4,
                "provider_attempts": [
                    {
                        "provider_id": "groq",
                        "model": "openai/gpt-oss-20b",
                        "attempt": 1,
                        "outcome": "SUCCESS",
                        "duration_ms": 10.0,
                        "status_code": 200,
                        "contacted": True,
                    },
                    {
                        "provider_id": "groq",
                        "model": "openai/gpt-oss-20b",
                        "attempt": 1,
                        "outcome": "RETRYABLE_HTTP",
                        "duration_ms": 10.0,
                        "status_code": 429,
                        "contacted": True,
                    },
                ],
            }
        ],
    }
    investigation = _investigation(
        verifier_status=None,
        case_status="INVESTIGATION_FAILED",
        engine_status="FAILED",
    )
    settings = SimpleNamespace(groq_investigator_model="openai/gpt-oss-20b")
    policy = SimpleNamespace(watchdog_timeout_s=80.0)

    verdict = runner.judge(
        summary,
        investigation,
        settings,
        policy,
        1.0,
        _passing_tool_safety(runner),
    )

    assert verdict["checks"]["deterministic_final_outcome"] is False
    assert verdict["live_provider_acceptance"] == "FAIL"
    assert verdict["outcome"] == "NOT_ACCEPTED"


def test_verifier_pass_with_dry_run_and_resolution_is_accepted(runner: ModuleType) -> None:
    investigation = _investigation(
        verifier_status="PASS",
        case_status="APPROVAL_REQUIRED",
        engine_status="RESOLVED",
        proof=object(),
        dry_run=object(),
    )
    checks = _judge(runner, investigation, _passing_tool_safety(runner))["checks"]
    assert checks["no_dry_run_without_verifier_pass"] is True
    assert checks["no_resolution_without_verifier_pass"] is True
    assert checks["ambiguity_left_unresolved"] is True


# ---------------------------------------------------------------------------
# Finding 6 - database lifecycle
# ---------------------------------------------------------------------------


def test_database_is_closed_on_the_success_path(runner: ModuleType, tmp_path: Path) -> None:
    database_path = tmp_path / "acceptance.sqlite3"
    result = runner.with_database(database_path, lambda database: database.schema_version)
    assert isinstance(result, int)
    # Windows refuses to rename a file whose handle is still open.
    moved = tmp_path / "moved.sqlite3"
    os.replace(database_path, moved)
    assert moved.is_file()
    moved.unlink()


def test_database_is_closed_when_the_work_raises(runner: ModuleType, tmp_path: Path) -> None:
    database_path = tmp_path / "acceptance.sqlite3"

    def explode(_database: Any) -> None:
        raise RuntimeError("work failed")

    with pytest.raises(RuntimeError):
        runner.with_database(database_path, explode)

    moved = tmp_path / "moved.sqlite3"
    os.replace(database_path, moved)
    assert moved.is_file()
    moved.unlink()


# ---------------------------------------------------------------------------
# The suite itself must never reach the network
# ---------------------------------------------------------------------------


def test_no_test_here_contacts_groq(runner: ModuleType) -> None:
    """Guard: the module exposes the live call only behind an explicit flag."""
    parser = runner.build_parser()
    defaults = parser.parse_args(["--work-dir", "unused"])
    assert defaults.execute_live_call is False
    source = RUNNER_PATH.read_text(encoding="utf-8")
    assert "args.execute_live_call" in source


# ---------------------------------------------------------------------------
# REVIEW-P1a - every user-controlled path is validated BEFORE anything exists
# ---------------------------------------------------------------------------


@pytest.fixture
def sentinel_settings(runner: ModuleType) -> Any:
    """Real Settings with a SENTINEL Groq key, so no test reads the owner's key."""
    from pydantic import SecretStr

    from app.config import Settings

    return Settings().model_copy(
        update={
            "ai_provider": "groq",
            "groq_api_key": SecretStr(SENTINEL_KEY),
            "gemini_api_key": None,
            "openai_api_key": None,
            "sarvam_api_key": None,
            "ollama_enabled": False,
        }
    )


def _console(runner: ModuleType) -> Any:
    return runner.SafeConsole(SENTINEL_KEY, stream=io.StringIO())


def test_unsafe_output_path_writes_no_report_and_no_temporary(
    runner: ModuleType, tmp_path: Path
) -> None:
    """persist_report must refuse an unsafe output BEFORE writing anything."""
    database = tmp_path / "acceptance.sqlite3"
    database.write_bytes(b"clean database bytes")
    output = tmp_path / f"report-{SENTINEL_KEY}.json"

    with pytest.raises(runner.SecretLeakError) as caught:
        runner.persist_report(
            {"outcome": "PREFLIGHT_ONLY"},
            needle=SENTINEL_KEY,
            database_path=database,
            console=_console(runner),
            output=output,
        )

    assert not output.exists()
    # No atomic-write temporary was staged either.
    assert list(tmp_path.glob(".w-*")) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["acceptance.sqlite3"]
    assert SENTINEL_KEY not in str(caught.value)


def test_unsafe_work_directory_creates_no_directory_or_database(
    runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sentinel_settings: Any
) -> None:
    monkeypatch.setattr(runner, "build_groq_only_settings", lambda: sentinel_settings)
    unsafe = tmp_path / SENTINEL_KEY

    with pytest.raises(runner.SecretLeakError):
        runner.main(["--work-dir", str(unsafe)], console=_console(runner))

    assert not unsafe.exists(), "an unsafe work directory was created"
    assert not (unsafe / "acceptance.sqlite3").exists()
    assert list(tmp_path.iterdir()) == []


def test_unsafe_dataset_argument_is_refused_before_reading_or_copying(
    runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sentinel_settings: Any
) -> None:
    monkeypatch.setattr(runner, "build_groq_only_settings", lambda: sentinel_settings)

    def explode(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the dataset was read or copied despite an unsafe argument")

    monkeypatch.setattr(runner, "stage_snapshot", explode)
    monkeypatch.setattr(runner, "ingest_inputs", explode)
    work = tmp_path / "work"

    with pytest.raises(runner.SecretLeakError):
        runner.main(
            ["--work-dir", str(work), "--dataset", f"datasets/{SENTINEL_KEY}"],
            console=_console(runner),
        )

    assert not work.exists()


def test_unsafe_paths_never_reach_stdout_or_stderr(
    runner: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sentinel_settings: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(runner, "build_groq_only_settings", lambda: sentinel_settings)
    code = runner.cli_main(["--work-dir", str(tmp_path / SENTINEL_KEY)])
    captured = capsys.readouterr()
    assert code == runner.EXIT_ABORTED
    assert SENTINEL_KEY not in captured.out
    assert SENTINEL_KEY not in captured.err
    assert "Traceback" not in captured.err
    assert runner.ABORT_MESSAGES["SecretLeakError"] in captured.out


def test_planned_success_messages_are_validated_before_persistence(
    runner: ModuleType, tmp_path: Path
) -> None:
    """Nothing printed after the atomic write can fail a secret check."""
    database = tmp_path / "acceptance.sqlite3"
    database.write_bytes(b"clean database bytes")
    output = tmp_path / "acceptance-report.json"
    console = _console(runner)

    runner.persist_report(
        {"outcome": "PREFLIGHT_ONLY"},
        needle=SENTINEL_KEY,
        database_path=database,
        console=console,
        output=output,
    )

    # The planned lines were scanned as part of the pre-write scan.
    persisted = json.loads(output.read_text(encoding="utf-8"))
    scanned = persisted["leak_scan"]["scanned"]
    assert any(name.startswith("planned-console[") for name in scanned)
    # The success line is a fixed constant and leaks no path.
    assert console.messages[-1] == runner.REPORT_WRITTEN_MESSAGE
    assert str(output) not in " ".join(console.messages)
    assert list(tmp_path.glob(".w-*")) == []


# ---------------------------------------------------------------------------
# REVIEW-P1b - the real CLI boundary leaks no exception body or traceback
# ---------------------------------------------------------------------------


def test_cli_acceptance_error_with_sentinel_exits_safely(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The ACTUAL cli_main path, not an imitation."""

    def raising_main(*_args: Any, **_kwargs: Any) -> int:
        raise runner.AcceptanceError(f"unsafe {SENTINEL_KEY}")

    monkeypatch.setattr(runner, "main", raising_main)
    console = _console(runner)
    code = runner.cli_main(["--work-dir", "unused"], console=console)
    captured = capsys.readouterr()

    assert code == runner.EXIT_ABORTED
    assert SENTINEL_KEY not in captured.out
    assert SENTINEL_KEY not in captured.err
    assert "unsafe " not in captured.out
    assert "Traceback" not in captured.err
    assert "During handling of the above exception" not in captured.err
    assert console.messages == [runner.ABORT_MESSAGES["AcceptanceError"]]
    assert SENTINEL_KEY not in " ".join(console.messages)


def test_cli_unexpected_exception_with_sentinel_exits_safely(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def raising_main(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError(f"Authorization: Bearer {SENTINEL_KEY}")

    monkeypatch.setattr(runner, "main", raising_main)
    console = _console(runner)
    code = runner.cli_main(["--work-dir", "unused"], console=console)
    captured = capsys.readouterr()

    assert code == runner.EXIT_ABORTED
    for stream in (captured.out, captured.err):
        assert SENTINEL_KEY not in stream
        assert "Authorization" not in stream
        assert "Bearer" not in stream
    assert "Traceback" not in captured.err
    assert console.messages == [runner.ABORT_MESSAGES["UnexpectedError"]]


def test_secret_leak_error_while_handling_another_exception_exposes_nothing(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A console that refuses even the fixed abort line must still exit cleanly."""

    class AlwaysRefusingConsole(runner.SafeConsole):  # type: ignore[misc, name-defined]
        def is_unsafe(self, message: str) -> bool:
            return True

    def raising_main(*_args: Any, **_kwargs: Any) -> int:
        raise runner.AcceptanceError(f"original secret-bearing failure {SENTINEL_KEY}")

    monkeypatch.setattr(runner, "main", raising_main)
    console = AlwaysRefusingConsole(SENTINEL_KEY, stream=io.StringIO())
    code = runner.cli_main(["--work-dir", "unused"], console=console)
    captured = capsys.readouterr()

    assert code == runner.EXIT_ABORTED
    assert SENTINEL_KEY not in captured.out
    assert SENTINEL_KEY not in captured.err
    assert "original secret-bearing failure" not in captured.out
    assert "original secret-bearing failure" not in captured.err
    assert "Traceback" not in captured.err
    # The refusal was recorded as the fixed placeholder, not the message.
    assert console.messages == [runner.REJECTED_MESSAGE_PLACEHOLDER]


def test_cli_returns_zero_on_a_clean_preflight(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "main", lambda *_a, **_k: runner.EXIT_OK)
    assert runner.cli_main(["--work-dir", "unused"], console=_console(runner)) == runner.EXIT_OK


# ---------------------------------------------------------------------------
# REVIEW-P2 - fail-closed proof that the preflight performs no network I/O
# ---------------------------------------------------------------------------


def test_preflight_completes_with_the_network_boundary_patched_to_fail(
    runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sentinel_settings: Any
) -> None:
    """Replace the real network boundary with a tripwire and prove zero calls.

    ``urllib.request.urlopen`` is exactly what ``app.ai.base.urllib_transport``
    calls, and ``socket.create_connection`` is what any other HTTP client would
    reach. Both raise immediately here, so any network attempt fails the test
    rather than silently succeeding.
    """
    import socket
    import urllib.request

    calls: list[str] = []

    def no_urlopen(*_args: Any, **_kwargs: Any) -> Any:
        calls.append("urlopen")
        raise AssertionError("the offline preflight attempted a network request")

    def no_connect(*_args: Any, **_kwargs: Any) -> Any:
        calls.append("socket")
        raise AssertionError("the offline preflight opened a socket")

    monkeypatch.setattr(urllib.request, "urlopen", no_urlopen)
    monkeypatch.setattr(socket, "create_connection", no_connect)
    monkeypatch.setattr(runner, "build_groq_only_settings", lambda: sentinel_settings)

    work = tmp_path / "work"
    console = _console(runner)
    exit_code = runner.main(["--work-dir", str(work)], console=console)

    assert exit_code == runner.EXIT_OK
    assert calls == [], f"the preflight touched the network: {calls}"

    report = json.loads((work / "acceptance-report.json").read_text(encoding="utf-8"))
    assert report["outcome"] == "PREFLIGHT_ONLY"
    assert report["live_call_executed"] is False
    assert report["leak_scan"]["clean"] is True
    assert report["provider_identity"]["chain"] == ["groq"]
    # The sentinel key never reaches the report or the console.
    assert SENTINEL_KEY not in (work / "acceptance-report.json").read_text(encoding="utf-8")
    assert SENTINEL_KEY not in " ".join(console.messages)
    # Exactly one report file was produced, with no atomic-write leftovers.
    assert list(work.glob(".w-*")) == []
    assert sorted(p.name for p in work.iterdir() if p.is_file()) == [
        "acceptance-report.json",
        "acceptance.sqlite3",
    ]


def test_network_tripwire_is_not_vacuous(
    runner: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard above only means something if the tripwire really fires."""
    import urllib.request

    calls: list[str] = []

    def no_urlopen(*_args: Any, **_kwargs: Any) -> Any:
        calls.append("urlopen")
        raise AssertionError("network attempted")

    monkeypatch.setattr(urllib.request, "urlopen", no_urlopen)

    with pytest.raises(AssertionError):
        runner.urllib_transport(
            "POST",
            "https://api.groq.com/openai/v1/chat/completions",
            {"Content-Type": "application/json"},
            b"{}",
            1.0,
        )
    assert calls == ["urlopen"]


# ---------------------------------------------------------------------------
# REVIEW-P1c - argparse must never print a rejected token
# ---------------------------------------------------------------------------

# Installed into a subprocess through PYTHONPATH. Python imports
# ``sitecustomize`` automatically at startup, so the tripwire is armed before
# the runner's first line executes. It records a marker file and raises, which
# turns any network attempt into a hard, observable failure.
_TRIPWIRE_SITECUSTOMIZE = r"""
import os
import socket
import urllib.request

_marker = os.environ.get("ARGUS_NETWORK_TRIPWIRE")


def _trip(*_args, **_kwargs):
    if _marker:
        with open(_marker, "a", encoding="utf-8") as handle:
            handle.write("network\n")
    raise AssertionError("the runner attempted a network request")


socket.create_connection = _trip
urllib.request.urlopen = _trip
try:
    socket.socket.connect = _trip
except (AttributeError, TypeError):
    pass
"""


def _run_cli(tmp_path: Path, args: list[str]) -> tuple[subprocess.CompletedProcess[str], bool]:
    """Run the real script in a subprocess with the network boundary armed."""
    tripwire_dir = tmp_path / "tripwire"
    tripwire_dir.mkdir()
    (tripwire_dir / "sitecustomize.py").write_text(_TRIPWIRE_SITECUSTOMIZE, encoding="utf-8")
    marker = tmp_path / "network-was-touched.txt"

    env = os.environ.copy()
    env["PYTHONPATH"] = str(tripwire_dir)
    env["ARGUS_NETWORK_TRIPWIRE"] = str(marker)

    process = subprocess.run(
        [sys.executable, str(RUNNER_PATH), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=300,
    )
    # A broken tripwire would make every "no network" assertion vacuous.
    assert "Error in sitecustomize" not in process.stderr, "the tripwire failed to load"
    return process, marker.exists()


def test_unknown_key_shaped_argument_is_never_printed(runner: ModuleType, tmp_path: Path) -> None:
    """The reproduction from REVIEW-P1c, driven through the real CLI."""
    work = tmp_path / "work"
    output = tmp_path / "report.json"
    process, network_touched = _run_cli(
        tmp_path,
        ["--work-dir", str(work), "--output", str(output), f"--{SENTINEL_KEY}"],
    )

    # Booleans are computed first so a failure message can never carry the token.
    leaked_stdout = SENTINEL_KEY in process.stdout
    leaked_stderr = SENTINEL_KEY in process.stderr
    has_traceback = "Traceback" in process.stderr
    has_chaining = "During handling of the above exception" in process.stderr
    mentions_unrecognized = "unrecognized arguments" in process.stderr

    assert process.returncode == runner.EXIT_ABORTED
    assert not leaked_stdout, "the rejected token appeared in stdout"
    assert not leaked_stderr, "the rejected token appeared in stderr"
    assert not has_traceback
    assert not has_chaining
    assert not mentions_unrecognized
    assert runner.ABORT_MESSAGES["ArgumentError"] in process.stdout

    # Nothing was created.
    assert not work.exists()
    assert not output.exists()
    assert not (work / "acceptance.sqlite3").exists()
    assert list(tmp_path.glob("*.json")) == []
    assert list(tmp_path.glob(".w-*")) == []
    assert not network_touched


def test_missing_required_argument_goes_through_the_safe_boundary(
    runner: ModuleType, tmp_path: Path
) -> None:
    process, network_touched = _run_cli(tmp_path, [])

    has_traceback = "Traceback" in process.stderr
    assert process.returncode == runner.EXIT_ABORTED
    assert runner.ABORT_MESSAGES["ArgumentError"] in process.stdout
    assert process.stderr.strip() == ""
    assert not has_traceback
    assert not network_touched


def test_help_still_exits_zero_with_readable_text(runner: ModuleType, tmp_path: Path) -> None:
    process, network_touched = _run_cli(tmp_path, ["--help"])

    assert process.returncode == runner.EXIT_OK
    assert "usage:" in process.stdout
    for flag in ("--work-dir", "--dataset", "--output", "--execute-live-call"):
        assert flag in process.stdout
    assert process.stderr.strip() == ""
    assert "Traceback" not in process.stderr
    assert SENTINEL_KEY not in process.stdout
    # --help mutates nothing and contacts nothing.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["tripwire"]
    assert not network_touched


def test_safe_parser_raises_instead_of_printing(runner: ModuleType) -> None:
    """Unit-level: the parser boundary itself, with no subprocess."""
    parser = runner.build_parser()
    with pytest.raises(runner.ArgumentParseError) as caught:
        parser.parse_args(["--work-dir", "x", f"--{SENTINEL_KEY}"])
    carried = SENTINEL_KEY in str(caught.value)
    assert not carried, "the exception carried the rejected token"


def test_safe_parser_help_exits_zero(runner: ModuleType) -> None:
    parser = runner.build_parser()
    with pytest.raises(SystemExit) as caught:
        parser.parse_args(["--help"])
    assert caught.value.code == runner.EXIT_OK


def test_safe_parser_never_prints_key_shaped_messages(
    runner: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = runner.build_parser()
    parser._print_message(f"usage: something {SENTINEL_KEY}\n", sys.stdout)
    captured = capsys.readouterr()
    leaked = SENTINEL_KEY in captured.out or SENTINEL_KEY in captured.err
    assert not leaked
    # A safe message still gets through, so --help stays readable.
    parser._print_message("usage: safe text\n", sys.stdout)
    assert "usage: safe text" in capsys.readouterr().out


def test_subprocess_tripwire_is_armed_and_records(tmp_path: Path) -> None:
    """Prove the subprocess tripwire is not vacuous.

    The probe targets 127.0.0.1 port 1 so that even an UNARMED tripwire could
    not reach an external host; nothing here leaves the machine.
    """
    tripwire_dir = tmp_path / "tripwire"
    tripwire_dir.mkdir()
    (tripwire_dir / "sitecustomize.py").write_text(_TRIPWIRE_SITECUSTOMIZE, encoding="utf-8")
    marker = tmp_path / "network-was-touched.txt"

    env = os.environ.copy()
    env["PYTHONPATH"] = str(tripwire_dir)
    env["ARGUS_NETWORK_TRIPWIRE"] = str(marker)

    probe = "import urllib.request; urllib.request.urlopen('http://127.0.0.1:1/')"
    process = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    assert "Error in sitecustomize" not in process.stderr
    assert process.returncode != 0
    assert "the runner attempted a network request" in process.stderr
    assert marker.exists(), "the tripwire did not record the attempt"
