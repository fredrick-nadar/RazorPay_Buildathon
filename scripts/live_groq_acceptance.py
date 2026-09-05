"""Chunk 2 LIVE GROQ ACCEPTANCE runner (cloud-reference sections 19, 20.8, 33, 34).

The deferred Chunk 2 gate cannot be closed by scripted transports: they prove
the deadline arithmetic and the attempt classification, never the owner's key,
quota, model access or real Groq latency.  This runner performs exactly ONE
bounded live Groq investigation of ONE genuine residual synthetic case, using
the production services only:

    ingest_inputs -> reconcile -> verify_cases   (app.runs._compute_run_outputs)
    resolve_investigator("groq")                 (app.ai.selection)
    investigate_cases                            (app.investigator.engine)
    verify_case                                  (called by the engine)

There is NO parallel agent architecture here, no scripted or wrapped transport,
no alternative prompt, no alternative tool dispatcher and no alternative
verifier.  The runner only selects one case, invokes production code, and then
judges the resulting evidence deterministically.

Safety properties:

- The live call happens only with ``--execute-live-call``.  Without it the
  runner performs every offline pre-flight check and stops.
- Groq-only: the chain must resolve to exactly ``["groq"]`` and its endpoint
  must parse to exactly ``https://api.groq.com/openai/v1`` - scheme, hostname,
  absence of credentials/port, and normalized base path are all checked with
  ``urllib.parse``.  Prefix matching is not sufficient and is not used.
- The API key value IS read into memory.  It has to be: the production Groq
  backend needs it to build the ``Authorization`` header, and the leak scan
  needs it as an exact-match needle.  The accurate guarantee is narrower and
  enforced here: the value is never displayed, logged, persisted, hashed, or
  included in any report or exception.  Only its PRESENCE is ever reported.
- Every console line goes through :class:`SafeConsole`, which refuses to print
  a message containing the exact key or a key-shaped token and records the
  exact strings it did print so they can be scanned.
- The report is written ONCE, atomically, and only after its exact final
  serialized bytes have passed the leak scan.  A failed scan aborts without
  creating or updating any report file.
- The temporary SQLite database is closed in a ``finally`` block before it is
  scanned, before the report is persisted, and before any cleanup.
- The runner never mutates financial truth logic and never marks a case
  resolved: the deterministic engine and verifier own every outcome.

Usage:

    .venv\\Scripts\\python scripts/live_groq_acceptance.py --work-dir <dir>
    .venv\\Scripts\\python scripts/live_groq_acceptance.py --work-dir <dir> \\
        --execute-live-call
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import shutil
import stat
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.ai.base import urllib_transport
from app.ai.selection import (
    InvestigatorUnavailableError,
    resolve_investigator,
)
from app.config import Settings
from app.domain.enums import CaseStatus
from app.importers.ingest import ingest_inputs
from app.importers.session_staging import _atomic_write as atomic_write
from app.investigator.engine import investigate_cases
from app.investigator.tools import TOOL_ALLOWLIST, ToolDispatcher
from app.persistence.database import Database
from app.runs import _compute_run_outputs, execute_run
from app.verifier.snapshot import build_evidence_snapshot

# Canonical Groq identity this acceptance requires, checked by URL parsing.
GROQ_PROVIDER_ID = "groq"
GROQ_HOSTNAME = "api.groq.com"
GROQ_BASE_PATH = "/openai/v1"

# Names an authority/write tool would plausibly use.  None of these may exist
# in the allowlist, and each must be refused by the real dispatcher.
FORBIDDEN_TOOL_NAMES = (
    "approve",
    "approve_correction",
    "apply",
    "apply_correction",
    "update_ledger",
    "write_ledger_entry",
    "mark_resolved",
    "resolve_case",
    "set_case_status",
    "delete_record",
)

# Substrings that would betray a write-shaped tool name in the allowlist.
WRITE_SHAPED_TOKENS = (
    "approve",
    "apply",
    "write",
    "update",
    "resolve",
    "delete",
    "set_",
)

# Only cases deterministic reconciliation left genuinely residual are eligible;
# anything already decided is skipped by the engine.
RESIDUAL_STATUSES = (CaseStatus.UNRESOLVED, CaseStatus.VERIFICATION_FAILED)

# A case in any of these states has been RESOLVED by the deterministic path and
# may exist only behind a verifier PASS.  Everything else is an open outcome.
RESOLVED_CASE_STATUSES = frozenset(
    {
        CaseStatus.VERIFIED_RESOLVED.value,
        CaseStatus.APPROVAL_REQUIRED.value,
        CaseStatus.SIMULATED_APPLIED.value,
    }
)

SECRET_LIKE = re.compile(r"\b(gsk_[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{20,})\b")

# Fixed placeholder recorded in place of a message the console refused.  It is
# a constant, so it can never carry the material that caused the refusal.
REJECTED_MESSAGE_PLACEHOLDER = "<message refused by SafeConsole: key-shaped content>"

# Fixed success lines.  They interpolate no user-controlled value - in
# particular not the output path - so nothing after the atomic write can fail
# a secret check.  The report location is already in the report and in the
# --work-dir the operator supplied.
REPORT_WRITTEN_MESSAGE = "report written: one file, atomically, after a clean leak scan"
NO_KEY_MESSAGE = (
    "Configure ARGUS_GROQ_API_KEY (or GROQ_API_KEY) in the gitignored "
    ".env.local, or export it in this shell. Never paste it into chat."
)

# Process exit codes.
EXIT_OK = 0
EXIT_NOT_ACCEPTED = 1
EXIT_ABORTED = 2

# Fixed abort lines, one per classification.  No exception body is ever
# interpolated into any of them.
ABORT_MESSAGES = {
    "SecretLeakError": "ACCEPTANCE ABORTED [SecretLeakError]: key-shaped material refused",
    "AcceptanceError": "ACCEPTANCE ABORTED [AcceptanceError]: a pre-flight or evidence check failed",
    "ArgumentError": (
        "ACCEPTANCE ABORTED [ArgumentError]: the command line was rejected; "
        "run with --help for usage"
    ),
    "UnexpectedError": "ACCEPTANCE ABORTED [UnexpectedError]: details withheld",
}


class AcceptanceError(RuntimeError):
    """A pre-flight or evidence check failed; the gate cannot be claimed."""


class SecretLeakError(AcceptanceError):
    """Key-shaped material reached output or a report; nothing is persisted.

    The offending text is deliberately NOT carried on the exception.
    """


class ArgumentParseError(AcceptanceError):
    """The command line was rejected.

    ``argparse`` builds its error text by interpolating the offending token
    (``error: unrecognized arguments: --gsk_...``) and writes it straight to
    stderr before any ARGUS code runs.  This exception replaces that path and
    deliberately carries NO argument, token or parser message.
    """


class SafeConsole:
    """Console that refuses key-shaped output and records what it printed.

    ``messages`` holds the exact strings handed to ``print``, so the final leak
    scan covers real emitted output rather than a reconstruction of it.
    """

    def __init__(self, needle: str = "", stream: Any = None) -> None:
        self._needle = needle.strip()
        self._stream = stream
        self.messages: list[str] = []

    def is_unsafe(self, message: str) -> bool:
        if self._needle and self._needle in message:
            return True
        return bool(SECRET_LIKE.search(message))

    def emit(self, message: str) -> None:
        """Print and record, or refuse before printing anything."""
        if self.is_unsafe(message):
            # Recorded as a constant so the transcript shows the refusal
            # without reproducing a single byte of what was refused.
            self.messages.append(REJECTED_MESSAGE_PLACEHOLDER)
            raise SecretLeakError(
                "refused to print a message containing key-shaped material"
            )
        self.messages.append(message)
        if self._stream is None:
            print(message, flush=True)
        else:
            print(message, file=self._stream, flush=True)


def is_secret_shaped(value: str, needle: str = "") -> bool:
    """Does this text carry the configured key or any supported key shape?"""
    if needle and needle.strip() and needle.strip() in value:
        return True
    return bool(SECRET_LIKE.search(value))


def assert_safe_text(label: str, value: str, needle: str) -> None:
    """Refuse key-shaped content in a user-controlled value.

    ``label`` names WHICH input was refused; ``value`` is never included in the
    exception, the message, or anything derived from them.
    """
    if is_secret_shaped(value, needle):
        raise SecretLeakError(f"{label} contains key-shaped material and was refused")


@dataclass(frozen=True)
class ValidatedPaths:
    """Every filesystem path this run will touch, proven safe before use."""

    work_dir: Path
    dataset_inputs: Path
    database_path: Path
    output: Path


def validate_paths(args: argparse.Namespace, needle: str) -> ValidatedPaths:
    """Validate every user-controlled path BEFORE anything is created.

    Nothing here creates a directory, copies a snapshot, opens a database or
    writes a report: ``Path.resolve`` is pure.  A refused path therefore leaves
    no directory, no database, no temporary report, no final report and no
    console line carrying the offending value (REVIEW-P1a).
    """
    work_dir = Path(args.work_dir).resolve()
    dataset_inputs = (REPO_ROOT / args.dataset / "inputs").resolve()
    database_path = work_dir / "acceptance.sqlite3"
    output = (
        Path(args.output).resolve()
        if args.output
        else work_dir / "acceptance-report.json"
    )

    # Raw arguments AND their resolved forms: a safe-looking relative argument
    # must not become unsafe once anchored, and vice versa.
    candidates: tuple[tuple[str, str], ...] = (
        ("--work-dir argument", str(args.work_dir)),
        ("--work-dir resolved path", str(work_dir)),
        ("--dataset argument", str(args.dataset)),
        ("--dataset resolved path", str(dataset_inputs)),
        ("--output argument", str(args.output or "")),
        ("resolved report path", str(output)),
        ("temporary database path", str(database_path)),
    )
    for label, value in candidates:
        assert_safe_text(label, value, needle)
    return ValidatedPaths(
        work_dir=work_dir,
        dataset_inputs=dataset_inputs,
        database_path=database_path,
        output=output,
    )


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _serialize(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# Pre-flight: configuration, Groq-only chain, tool safety, synthetic snapshot
# ---------------------------------------------------------------------------


def build_groq_only_settings() -> Settings:
    """Resolve settings and force Groq-only provider selection."""
    settings = Settings()
    return settings.model_copy(update={"ai_provider": GROQ_PROVIDER_ID})


def secret_needle(settings: Any) -> str:
    """The configured key value, for in-memory exact-match scanning only."""
    secret = getattr(settings, "groq_api_key", None)
    if secret is None:
        return ""
    return str(secret.get_secret_value()).strip()


def key_is_present(settings: Any) -> bool:
    """Report PRESENCE only.

    The secret value is read in memory here (and by the production backend, and
    by the leak scan) but is never returned, displayed, logged or persisted.
    """
    return bool(secret_needle(settings))


def stage_snapshot(dataset_inputs: Path, work_dir: Path) -> tuple[Path, dict[str, str]]:
    """Copy the synthetic inputs into an immutable, hashed working snapshot."""
    snapshot = work_dir / "inputs"
    if snapshot.exists():
        raise AcceptanceError("the working snapshot directory already exists")
    shutil.copytree(dataset_inputs, snapshot)
    digests: dict[str, str] = {}
    for path in sorted(snapshot.rglob("*")):
        if not path.is_file():
            continue
        digests[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        # Immutable for the duration of the acceptance.
        path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    if not digests:
        raise AcceptanceError("synthetic snapshot is empty")
    return snapshot, digests


def validate_groq_endpoint(base_url: str) -> str:
    """Parse and validate the Groq endpoint; return its normalized base path.

    Prefix matching cannot express this: it accepts any path under the host
    (``https://api.groq.com/evil/v1``) and its rejection of a look-alike host
    is incidental rather than decided.  Every component is checked explicitly.
    """
    parts = urlsplit(base_url)
    if parts.scheme != "https":
        raise AcceptanceError(
            f"Groq endpoint must use https, got scheme {parts.scheme!r}"
        )
    if parts.username or parts.password:
        raise AcceptanceError("Groq endpoint must not carry embedded credentials")
    try:
        port = parts.port
    except ValueError as exc:  # malformed port in the netloc
        raise AcceptanceError("Groq endpoint has an invalid port") from exc
    if port is not None:
        raise AcceptanceError(f"Groq endpoint must not specify a port, got {port}")
    if parts.hostname != GROQ_HOSTNAME:
        raise AcceptanceError(
            f"Groq endpoint hostname must be exactly {GROQ_HOSTNAME!r}, got {parts.hostname!r}"
        )
    if parts.query or parts.fragment:
        raise AcceptanceError("Groq endpoint must not carry a query string or fragment")
    normalized = posixpath.normpath(parts.path or "/")
    if normalized != GROQ_BASE_PATH:
        raise AcceptanceError(
            f"Groq endpoint base path must normalize to {GROQ_BASE_PATH!r}, got {normalized!r}"
        )
    return normalized


def assert_groq_only(provider: Any, settings: Any) -> dict[str, Any]:
    """Prove the resolved chain is Groq and only Groq, over the real transport."""
    member_ids = list(provider.chain.member_ids)
    if member_ids != [GROQ_PROVIDER_ID]:
        raise AcceptanceError(
            f"chain must be Groq-only for this acceptance; resolved {member_ids}"
        )
    member = provider.chain.members[0]
    base_url = str(getattr(member, "base_url", ""))
    normalized_path = validate_groq_endpoint(base_url)
    if getattr(member, "transport", None) is not urllib_transport:
        raise AcceptanceError(
            "Groq member is not using the production urllib transport; a scripted "
            "or wrapped transport cannot satisfy this gate"
        )
    if str(member.model) != settings.groq_investigator_model:
        raise AcceptanceError("Groq member model does not match the configured model")
    return {
        "chain": member_ids,
        "provider_id": provider.provider_id,
        "model": str(member.model),
        "base_url": base_url,
        "endpoint_hostname": GROQ_HOSTNAME,
        "endpoint_base_path": normalized_path,
        "endpoint_validated_by": "urllib.parse.urlsplit",
        "transport": "app.ai.base.urllib_transport",
        "policy_fingerprint": provider.policy_fingerprint,
        "policy": provider.policy.describe() if provider.policy is not None else None,
    }


def probe_authority_tools(tools: ToolDispatcher) -> dict[str, Any]:
    """Measure - never assume - that no authority/write tool is reachable.

    Returns the facts.  It does not raise, so ``judge`` can derive the verdict
    from a real measurement and a caller can fail fast on the same dict.
    """
    allowlist = sorted(TOOL_ALLOWLIST)
    banned_in_allowlist = sorted(set(allowlist) & set(FORBIDDEN_TOOL_NAMES))
    write_shaped = sorted(
        name
        for name in allowlist
        if any(token in name for token in WRITE_SHAPED_TOKENS)
    )
    accepted: list[str] = []
    refusal_codes: set[str] = set()
    for name in FORBIDDEN_TOOL_NAMES:
        try:
            observation = tools.dispatch(name, {})
        except Exception:  # noqa: BLE001 - a raising dispatcher is not a refusal
            accepted.append(name)
            continue
        code = str(observation.get("error", ""))
        if code != "UNKNOWN_TOOL":
            accepted.append(name)
            continue
        refusal_codes.add(code)
    return {
        "probe_executed": True,
        "allowlist": allowlist,
        "allowlist_size": len(allowlist),
        "banned_in_allowlist": banned_in_allowlist,
        "write_shaped_allowlist_names": write_shaped,
        "forbidden_probed": list(FORBIDDEN_TOOL_NAMES),
        "accepted_forbidden_tools": accepted,
        "forbidden_all_refused": not accepted,
        "refusal_code": sorted(refusal_codes),
    }


def authority_probe_passed(tool_safety: Any) -> bool:
    """Did a REAL probe run and find every authority/write tool unreachable?

    A missing, malformed or unexecuted probe is a failure, never a pass.
    """
    if not isinstance(tool_safety, dict):
        return False
    if tool_safety.get("probe_executed") is not True:
        return False
    if tool_safety.get("forbidden_all_refused") is not True:
        return False
    if tool_safety.get("accepted_forbidden_tools"):
        return False
    if tool_safety.get("banned_in_allowlist"):
        return False
    if tool_safety.get("write_shaped_allowlist_names"):
        return False
    probed = tool_safety.get("forbidden_probed")
    return isinstance(probed, list) and sorted(probed) == sorted(FORBIDDEN_TOOL_NAMES)


# ---------------------------------------------------------------------------
# Deterministic baseline and residual-case selection
# ---------------------------------------------------------------------------


def with_database[T](database_path: Path, work: Callable[[Database], T]) -> T:
    """Open, use, and DETERMINISTICALLY close the temporary database.

    Windows keeps a SQLite file locked while a connection is open, so the
    database must be closed before it is scanned, before the report is written
    and before any cleanup - on the exception path too.
    """
    database = Database(database_path)
    try:
        return work(database)
    finally:
        database.close()


def deterministic_baseline(snapshot: Path, database: Database) -> dict[str, Any]:
    """Persist the rules-only run through the production path, unchanged."""
    result = execute_run(snapshot, database, mode="rules-only")
    summary = result.summary
    return {
        "run_id": result.run_id,
        "status": result.status.value,
        "economic_output_hash": result.economic_output_hash,
        "cases_count": summary["cases_count"],
        "cases_by_category": summary["cases_by_category"],
    }


def select_residual_case(
    snapshot: Path, run_id: str
) -> tuple[Any, Any, dict[str, Any]]:
    """Recompute the run deterministically and pick ONE genuine residual case.

    Uses the exact production computation the run path uses, so the case ids
    match the persisted rules-only baseline.  Selection is deterministic: the
    lowest case id among cases the engine would actually investigate.
    """
    ingest = ingest_inputs(snapshot)
    _result, _totals, graph_json, _hash, verification, _inv = _compute_run_outputs(
        ingest, run_id, mode="rules-only"
    )
    cases = list(verification.cases)
    residual = sorted(
        (case for case in cases if case.status in RESIDUAL_STATUSES),
        key=lambda case: case.case_id,
    )
    if not residual:
        raise AcceptanceError(
            "the synthetic dataset left no residual case; there is nothing for a "
            "bounded investigator to investigate"
        )
    chosen = residual[0]
    context = {
        "total_cases": len(cases),
        "residual_case_count": len(residual),
        "residual_case_ids": [case.case_id for case in residual],
        "selected_case_id": chosen.case_id,
        "selected_category": chosen.category.value,
        "selected_status_before": chosen.status.value,
        "selected_variance_paise": chosen.variance_paise,
        "selected_evidence_count": len(chosen.evidence),
    }
    return ingest, (chosen, cases, graph_json), context


# ---------------------------------------------------------------------------
# The single bounded live investigation
# ---------------------------------------------------------------------------


def run_live_investigation(
    ingest: Any,
    chosen: Any,
    graph_json: dict[str, Any],
    provider: Any,
) -> tuple[dict[str, Any], Any, float]:
    """One case, one production engine call, one deterministic verifier."""
    started = time.perf_counter()
    outcome = investigate_cases(
        records=ingest.records,
        cases=[chosen],
        provider=provider,
        graph_json=graph_json,
    )
    duration_s = time.perf_counter() - started
    return outcome.summary(), outcome.investigations[0], duration_s


# ---------------------------------------------------------------------------
# Deterministic acceptance judgement
# ---------------------------------------------------------------------------


def judge(
    summary: dict[str, Any],
    investigation: Any,
    settings: Any,
    policy: Any,
    duration_s: float,
    tool_safety: Any = None,
) -> dict[str, Any]:
    """Evaluate every §19/§20.8 acceptance criterion from measured evidence."""
    case_entry = (summary.get("cases") or [{}])[0]
    attempts = list(case_entry.get("provider_attempts") or [])
    contacted = [item for item in attempts if bool(item.get("contacted", True))]
    successes = [item for item in attempts if item.get("outcome") == "SUCCESS"]
    model = settings.groq_investigator_model

    verifier_result = investigation.verifier_result
    verifier_status = (
        verifier_result.status.value if verifier_result is not None else None
    )
    verifier_passed = verifier_status == "PASS"
    case_status = investigation.case.status.value
    evidence_calls = int(case_entry.get("evidence_tool_calls", 0))

    # Tier 1 - everything the live Groq call itself must prove. These are
    # decidable from the measured evidence regardless of which final action the
    # model chose.
    live_checks: dict[str, bool] = {
        "selected_provider_is_groq": summary.get("provider_id") == "llm:groq",
        "groq_transport_contacted": any(
            item.get("provider_id") == GROQ_PROVIDER_ID for item in contacted
        ),
        "actual_provider_is_groq": summary.get("actual_providers")
        == [GROQ_PROVIDER_ID],
        "no_non_groq_provider_involved": all(
            item.get("provider_id") == GROQ_PROVIDER_ID for item in attempts
        ),
        "configured_groq_model_returned": any(
            item.get("provider_id") == GROQ_PROVIDER_ID and item.get("model") == model
            for item in successes
        ),
        "case_bound_evidence_tool_used": evidence_calls >= 1,
        # Derived from the REAL dispatcher probe, never assumed.
        "no_authority_tool_available": authority_probe_passed(tool_safety),
        # Provider contact and evidence calls are not a completed investigation.
        # A later timeout/rate-limit still leaves the engine in FAILED and must
        # never be reported as a live acceptance pass.
        "deterministic_final_outcome": investigation.status
        in {"RESOLVED", "UNRESOLVED"},
        "ambiguity_left_unresolved": (
            verifier_passed or case_status not in RESOLVED_CASE_STATUSES
        ),
        # A dry-run preview is a financial artefact and may exist only behind a
        # verifier PASS.  A proof package is NOT covered here: the engine builds
        # one for FAIL and INCONCLUSIVE too, and that is the product contract.
        "no_dry_run_without_verifier_pass": verifier_passed
        or investigation.dry_run is None,
        # No case may reach a resolved/authority state without a verifier PASS.
        "no_resolution_without_verifier_pass": verifier_passed
        or (
            case_status not in RESOLVED_CASE_STATUSES
            and investigation.status != "RESOLVED"
        ),
        "within_case_deadline": duration_s <= policy.watchdog_timeout_s,
        "attempt_trace_sanitized": _attempts_are_sanitized(attempts),
        "trace_sanitized": _trace_is_sanitized(case_entry.get("trace") or []),
    }

    # Tier 2 - the deterministic verifier path. A model that correctly answers
    # "unresolved" on an ambiguous case never produces a hypothesis, so
    # ``verify_case`` is legitimately never invoked. That is NOT OBSERVED, not
    # a failure: forcing a verifier result would mean changing the engine or
    # burning live calls until the model guesses, and both are prohibited.
    verifier_observed = verifier_status in {"PASS", "FAIL", "INCONCLUSIVE"}
    live_pass = all(live_checks.values())
    if live_pass and verifier_observed:
        outcome = "ACCEPTED"
    elif live_pass:
        outcome = "LIVE_PASS_VERIFIER_NOT_OBSERVED"
    else:
        outcome = "NOT_ACCEPTED"

    return {
        "outcome": outcome,
        "live_provider_acceptance": "PASS" if live_pass else "FAIL",
        "verifier_path_observation": "OBSERVED"
        if verifier_observed
        else "NOT OBSERVED",
        "chunk2_live_gate": "CLOSED"
        if (live_pass and verifier_observed)
        else "NOT YET CLOSED",
        "checks": live_checks,
        "verifier_status": verifier_status,
        "case_status_after": case_status,
        "engine_outcome": investigation.status,
        "failure_code": case_entry.get("failure_code"),
        "failure_reason": case_entry.get("failure_reason"),
        "evidence_tool_calls": evidence_calls,
        "tool_calls_used": int(case_entry.get("tool_calls_used", 0)),
        "retries_used": int(case_entry.get("retries_used", 0)),
        "live_http_attempts": len(contacted),
        "attempts": attempts,
    }


_ALLOWED_ATTEMPT_KEYS = {
    "provider_id",
    "model",
    "attempt",
    "outcome",
    "duration_ms",
    "status_code",
    "contacted",
}
_ALLOWED_TRACE_KEYS = {
    "step",
    "type",
    "provider",
    "model",
    "response_chars",
    "tool",
    "outcome",
    "result_keys",
    "identifiers",
    "case_evidence",
    "code",
}


def _attempts_are_sanitized(attempts: list[dict[str, Any]]) -> bool:
    for item in attempts:
        if set(item) - _ALLOWED_ATTEMPT_KEYS:
            return False
        if SECRET_LIKE.search(json.dumps(item, default=str)):
            return False
    return True


def _trace_is_sanitized(trace: list[dict[str, Any]]) -> bool:
    for step in trace:
        if set(step) - _ALLOWED_TRACE_KEYS:
            return False
        if SECRET_LIKE.search(json.dumps(step, default=str)):
            return False
    return True


# ---------------------------------------------------------------------------
# Leak scanning and single atomic report persistence
# ---------------------------------------------------------------------------


def leak_scan(
    needle: str, targets: list[Path], texts: dict[str, str]
) -> dict[str, Any]:
    """Prove no secret reached persisted bytes or emitted text.

    ``needle`` is the key value, read into memory by the caller - that is the
    only way to prove an exact-match absence. Neither it nor any offending text
    is printed, written, hashed or returned; only names and booleans are.
    """
    needle = (needle or "").strip()
    findings: list[str] = []
    scanned: list[str] = []
    for path in targets:
        if not path.is_file():
            continue
        scanned.append(path.name)
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        if needle and (needle in text or needle.encode("utf-8") in raw):
            findings.append(f"exact key material in {path.name}")
        if SECRET_LIKE.search(text):
            findings.append(f"secret-like token in {path.name}")
    for label, blob in texts.items():
        scanned.append(label)
        if needle and needle in blob:
            findings.append(f"exact key material in {label}")
        if SECRET_LIKE.search(blob):
            findings.append(f"secret-like token in {label}")
    return {"scanned": scanned, "clean": not findings, "findings": findings}


def persist_report(
    report: dict[str, Any],
    *,
    needle: str,
    database_path: Path,
    console: SafeConsole,
    output: Path,
) -> dict[str, Any]:
    """Scan first, write once, atomically - never a report before its scan.

    Order is the whole point:

    1. serialize the candidate report;
    2. scan the closed database, that exact serialization, every string the
       console actually printed, AND every line this function still plans to
       print;
    3. abort on any finding WITHOUT creating or updating a report file;
    4. attach non-secret leak-scan metadata, serialize again;
    5. rescan those exact final bytes;
    6. write once, atomically;
    7. print pre-validated fixed lines - no fallible check remains after the
       write (REVIEW-P1a).
    """
    # Defence in depth: ``validate_paths`` already refused an unsafe output at
    # the CLI boundary, but this function must also be safe when called
    # directly, and it must refuse BEFORE it writes anything.
    assert_safe_text("report output path", str(output), needle)

    candidate_text = _serialize(report)
    console_texts = {
        f"console[{index}]": line for index, line in enumerate(console.messages)
    }
    # Built BEFORE the write and scanned with everything else. They interpolate
    # only an integer count, never the output path.
    planned = (
        f"leak scan clean: True (targets scanned: {len(console_texts) + 2}, final rescanned)",
        REPORT_WRITTEN_MESSAGE,
    )
    planned_texts = {
        f"planned-console[{index}]": line for index, line in enumerate(planned)
    }
    scan = leak_scan(
        needle,
        [database_path],
        {"report-json": candidate_text, **console_texts, **planned_texts},
    )
    if not scan["clean"]:
        # Names of targets only; no offending text is ever surfaced.
        raise SecretLeakError(
            f"leak scan failed before persistence; no report written. targets: {scan['findings']}"
        )

    final = dict(report)
    final["leak_scan"] = {**scan, "final_bytes_rescanned": True}
    final_text = _serialize(final)
    rescan = leak_scan(needle, [], {"final-report-json": final_text})
    if not rescan["clean"]:
        raise SecretLeakError(
            f"final report bytes failed rescan; no report written. targets: {rescan['findings']}"
        )

    # Belt and braces: the planned lines were scanned above; prove each is
    # printable BEFORE the irreversible write, so the post-write emits below
    # are total and the file can never outlive a refused message.
    for line in planned:
        if console.is_unsafe(line):
            raise SecretLeakError(
                "a planned console line was refused; no report written"
            )

    atomic_write(output, final_text.encode("utf-8"))
    # Everything from here on is pre-validated and cannot fail a secret check.
    for line in planned:
        console.emit(line)
    return final


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


class SafeArgumentParser(argparse.ArgumentParser):
    """``argparse`` that never prints a rejected token or a parser error body.

    Stock ``argparse`` writes ``error: unrecognized arguments: <token>`` to
    stderr and raises ``SystemExit`` itself, so a mistakenly pasted key would
    be displayed before ``cli_main`` could sanitize anything (REVIEW-P1c).
    Three overrides close that, without ``parse_known_args`` and without
    silently ignoring an unknown option:

    * ``error``  - drop the interpolated message, raise the fixed exception.
    * ``exit``   - status 0 (``--help``) still exits 0; anything else raises.
    * ``_print_message`` - last-resort filter for any argparse path that writes
      directly, so nothing key-shaped can reach a stream.
    """

    def error(self, message: str) -> NoReturn:
        # ``message`` embeds the offending token. It is discarded, never
        # logged, and never attached to the exception.
        raise ArgumentParseError("the command line was rejected")

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        if status == 0:
            # --help has already written its own static text through
            # _print_message; nothing further may be printed here.
            raise SystemExit(0)
        raise ArgumentParseError("the command line was rejected")

    def _print_message(self, message: str, file: Any = None) -> None:
        # Help and usage text is static and safe; a token never is.
        if message and is_secret_shaped(message):
            return
        super()._print_message(message, file)


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(description="Chunk 2 live Groq acceptance runner")
    parser.add_argument("--dataset", default="datasets/dev")
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--output", default=None)
    parser.add_argument(
        "--execute-live-call",
        action="store_true",
        help="perform ONE bounded live Groq investigation (owner-authorized only)",
    )
    return parser


def collect_evidence(
    args: argparse.Namespace,
    settings: Any,
    console: SafeConsole,
    paths: ValidatedPaths,
) -> tuple[dict[str, Any], int]:
    """Everything that needs the database open. Returns (report, exit code).

    ``paths`` has already been proven free of key-shaped material, so every
    path interpolated into a message or the report below is safe by
    construction.
    """
    work_dir = paths.work_dir
    dataset_inputs = paths.dataset_inputs
    database_path = paths.database_path
    if not dataset_inputs.is_dir():
        # The path is validated, but say as little as possible regardless.
        raise AcceptanceError(
            "the requested synthetic dataset inputs directory does not exist"
        )

    report: dict[str, Any] = {
        "acceptance": "chunk-2-live-groq",
        "started_at_utc": _utc_now_iso(),
        "groq_api_key_present": True,
        "work_dir": str(work_dir),
        "dataset": str(dataset_inputs.relative_to(REPO_ROOT)),
        "live_call_executed": False,
    }

    snapshot, digests = stage_snapshot(dataset_inputs, work_dir)
    report["snapshot"] = {"path": str(snapshot), "sha256": digests, "read_only": True}
    console.emit(f"synthetic snapshot staged read-only: {snapshot}")

    if database_path.exists():
        raise AcceptanceError("the temporary acceptance database already exists")
    console.emit(f"isolated temporary database: {database_path}")

    def with_open_database(database: Database) -> tuple[dict[str, Any], int]:
        baseline = deterministic_baseline(snapshot, database)
        report["deterministic_baseline"] = baseline
        console.emit(
            f"rules-only baseline: {baseline['run_id']} {baseline['status']} "
            f"cases={baseline['cases_count']} {baseline['cases_by_category']}"
        )

        ingest, (chosen, cases, graph_json), case_context = select_residual_case(
            snapshot, baseline["run_id"]
        )
        report["case_selection"] = case_context
        console.emit(
            f"selected residual case: {case_context['selected_case_id']} "
            f"({case_context['selected_category']}, "
            f"{case_context['selected_status_before']}) of "
            f"{case_context['residual_case_count']} residual"
        )

        try:
            selection = resolve_investigator(settings, GROQ_PROVIDER_ID)
        except InvestigatorUnavailableError as exc:
            raise AcceptanceError(f"investigator unavailable: {exc}") from None
        provider = selection.provider
        identity = assert_groq_only(provider, settings)
        report["provider_identity"] = identity
        console.emit(
            f"groq-only chain confirmed: {identity['chain']} model={identity['model']}"
        )

        tools = ToolDispatcher(
            snapshot=build_evidence_snapshot(ingest.records),
            records=ingest.records,
            cases={case.case_id: case for case in cases},
            graph_json=graph_json,
        )
        tool_safety = probe_authority_tools(tools)
        report["tool_safety"] = tool_safety
        if not authority_probe_passed(tool_safety):
            raise AcceptanceError(
                "authority/write tool probe did not pass: "
                f"accepted={tool_safety['accepted_forbidden_tools']} "
                f"banned_in_allowlist={tool_safety['banned_in_allowlist']}"
            )
        console.emit(
            "tool safety: no authority/write tool in the allowlist; all probes refused"
        )

        policy = selection.policy
        if policy is None:
            raise AcceptanceError("investigator selection returned no execution policy")
        report["budgets"] = {
            "attempt_timeout_cap_s": policy.attempt_timeout_cap_s,
            "turn_deadline_s": policy.turn_deadline_s,
            "case_deadline_s": policy.case_deadline_s,
            "watchdog_timeout_s": policy.watchdog_timeout_s,
            "max_attempts_per_provider": policy.max_attempts_per_provider,
            "tool_call_budget": policy.tool_call_budget,
            "max_schema_attempts": policy.max_schema_attempts,
            "max_live_http_attempts_ceiling": (
                policy.tool_call_budget + policy.max_schema_attempts + 1
            ),
        }

        if not args.execute_live_call:
            report["outcome"] = "PREFLIGHT_ONLY"
            console.emit(
                "PREFLIGHT ONLY - no live call made. Re-run with --execute-live-call."
            )
            return report, 0

        console.emit("executing ONE bounded live Groq investigation ...")
        summary, investigation, duration_s = run_live_investigation(
            ingest, chosen, graph_json, provider
        )
        report["live_call_executed"] = True
        report["investigation_summary"] = summary
        report["duration_s"] = round(duration_s, 3)
        verdict = judge(
            summary, investigation, settings, policy, duration_s, tool_safety
        )
        report["verdict"] = verdict
        report["finished_at_utc"] = _utc_now_iso()
        report["outcome"] = verdict["outcome"]

        for name, value in sorted(verdict["checks"].items()):
            console.emit(f"  [{'PASS' if value else 'FAIL'}] {name}")
        console.emit(
            f"live groq/provider/tool-use acceptance: {verdict['live_provider_acceptance']}"
        )
        console.emit(
            f"verifier-path observation:              {verdict['verifier_path_observation']}"
        )
        console.emit(
            f"full chunk 2 live gate:                 {verdict['chunk2_live_gate']}"
        )
        console.emit(f"acceptance outcome: {report['outcome']} in {duration_s:.2f}s")
        return report, (0 if verdict["outcome"] == "ACCEPTED" else 1)

    return with_database(database_path, with_open_database)


def main(
    argv: list[str] | None = None,
    console: SafeConsole | None = None,
    args: argparse.Namespace | None = None,
) -> int:
    # ``args`` lets ``cli_main`` parse behind the safe parser boundary without
    # parsing twice; a direct caller still gets normal argv handling.
    if args is None:
        args = build_parser().parse_args(argv)

    settings = build_groq_only_settings()
    needle = secret_needle(settings)
    active = console if console is not None else SafeConsole(needle)

    # FIRST, before any directory, snapshot, database or report exists.
    paths = validate_paths(args, needle)

    active.emit(f"groq_api_key_present: {bool(needle)}")
    if not needle:
        active.emit(NO_KEY_MESSAGE)
        return EXIT_ABORTED

    paths.work_dir.mkdir(parents=True, exist_ok=True)

    # The database is closed by ``with_database`` before anything below runs, so
    # scanning, atomic persistence and cleanup never race a Windows file lock.
    report, exit_code = collect_evidence(args, settings, active, paths)

    persist_report(
        report,
        needle=needle,
        database_path=paths.database_path,
        console=active,
        output=paths.output,
    )
    return exit_code


def abort(console: SafeConsole | None, classification: str) -> int:
    """Print ONE fixed line for a classification and return the abort code.

    Never raises: a failure to print must not replace a clean nonzero exit with
    a traceback.  No exception body, argument or path reaches this path.
    """
    message = ABORT_MESSAGES.get(classification, ABORT_MESSAGES["UnexpectedError"])
    try:
        if console is not None:
            console.emit(message)
        else:
            print(message, flush=True)
    except Exception:  # noqa: BLE001,S110 - the abort path is the last defence
        # Deliberately silent: logging here could reintroduce the very content
        # the abort exists to withhold, and a raise would replace a clean
        # nonzero exit with a traceback.
        pass
    return EXIT_ABORTED


def cli_main(argv: list[str] | None = None, console: SafeConsole | None = None) -> int:
    """Outermost boundary: fixed classifications, no chaining, no traceback.

    No exception is bound to a name and no exception body is interpolated, so
    a ``SecretLeakError`` raised while another exception is being handled can
    never carry the original secret-bearing exception into a printed traceback
    (REVIEW-P1b).  Every path returns an int; nothing propagates to stderr.
    """
    active = console
    try:
        # Parsed FIRST, so --help is answered without touching settings and a
        # rejected token never reaches a stream (REVIEW-P1c).
        args = build_parser().parse_args(argv)
        if active is None:
            active = SafeConsole(secret_needle(build_groq_only_settings()))
        return main(argv, console=active, args=args)
    except ArgumentParseError:
        return abort(active, "ArgumentError")
    except SecretLeakError:
        return abort(active, "SecretLeakError")
    except AcceptanceError:
        return abort(active, "AcceptanceError")
    except Exception:  # noqa: BLE001 - never surface a raw body or a traceback
        return abort(active, "UnexpectedError")


if __name__ == "__main__":  # pragma: no cover - CLI entry
    os.environ.setdefault("ARGUS_AI_PROVIDER", GROQ_PROVIDER_ID)
    sys.exit(cli_main())
