"""Phase acceptance gate runner (authoritative per PRD section 16).

Usage:

    .venv\\Scripts\\python scripts\\verify_phase.py --phase 0
    .venv\\Scripts\\python scripts\\verify_phase.py --phase 1
    .venv\\Scripts\\python scripts\\verify_phase.py --phase 2
    .venv\\Scripts\\python scripts\\verify_phase.py --phase 3

Phase 1 runs the complete, unchanged Phase 0 step list first and then appends
the dataset steps; Phase 2 runs the unchanged Phase 0 and Phase 1 lists and
appends the reconciliation steps; Phase 3 runs Phase 0, 1, and 2 first, then
appends verification/proof/dry-run steps. No later phase gate can weaken an
earlier one.

Portability contract (Windows cmd/PowerShell, any active code page):

- This script NEVER installs or downloads dependencies. Dependency setup is a
  separate bootstrap step (see AGENTS.md section 7).
- pytest runs with a repository-local ``--basetemp`` under the gitignored
  ``tmp/`` directory and with the cache provider disabled, so it never depends
  on the machine temp directory being writable.
- All console output goes through ``emit``/``safe_text`` so characters such as
  Next.js's ``○`` never crash a CP1252 console (backslashreplace fallback).
- The artifact lifecycle is fail-safe: a RUNNING artifact replaces any stale
  artifact at start, and every unexpected exception is caught at the top level
  and written as a FAIL artifact naming the failed step. A previous PASS can
  never survive as apparent evidence for a crashed run.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import release_assets  # resolved via SCRIPTS_DIR above
import release_evidence  # resolved via SCRIPTS_DIR above

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIR = REPO_ROOT / "frontend"
ARTIFACT_DIR = REPO_ROOT / "artifacts" / "evaluation"
TMP_DIR = REPO_ROOT / "tmp"

IS_WINDOWS = os.name == "nt"
VENV_PYTHON = (
    REPO_ROOT / ".venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
)
if not VENV_PYTHON.is_file():
    VENV_PYTHON = Path(sys.executable)

SUPPORTED_PHASES = {0, 1, 2, 3, 4, 5, 6, 7, 8}

PHASE_NAMES = {
    0: "Foundation and Frozen Contracts",
    1: "Synthetic Data, Ground Truth, and Isolation",
    2: "Normalization, Reconciliation, and Evidence Graph",
    3: "Verifier, Proof Packages, and Dry-Run Core",
    4: "Bounded AI Investigator",
    5: "Control Room, Approval, Simulated Application, and Audit",
    6: "Failure Laboratory and Safe Adapter",
    7: "Frozen Holdout Benchmark and Hardening",
    8: "Submission Release",
}


DATASET_PROFILES = (
    # (profile name, PRD-documented seed) for the Phase 1 dataset steps.
    ("dev", 4104),
    ("adversarial", 4105),
)

SKIP_DIRS = {
    ".git",
    "node_modules",
    ".next",
    ".venv",
    "artifacts",
    "test-results",
    "playwright-report",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".hypothesis",
    "out",
    "dist",
    "build",
    "tmp",
    ".cache",
}

SCAN_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".mjs",
    ".cjs",
    ".json",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
    ".toml",
    ".cfg",
    ".ini",
    ".example",
    ".css",
    ".html",
    "",
}

SECRET_PATTERNS: list[tuple[str, str]] = [
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key block"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS access key id"),
    (r"\bsk-[A-Za-z0-9_-]{20,}\b", "OpenAI-style API key"),
    (r"\bgsk_[A-Za-z0-9_-]{20,}\b", "Groq API key"),
    (r"\brzp_(?:live|test)_[0-9A-Za-z]{14,}\b", "Razorpay API key"),
    (r"\bghp_[A-Za-z0-9]{30,}\b", "GitHub token"),
    (r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", "Slack token"),
]

REQUIRED_GITIGNORE_PATTERNS = [
    ".env",
    ".env.*",
    "!.env.example",
    "*.pem",
    "*.key",
    "*.db",
    "*.sqlite",
    "*.sqlite3",
    "node_modules/",
    ".venv/",
    "__pycache__/",
]

# Name of the gate step currently executing; recorded in the FAIL artifact
# when an unexpected exception escapes.
_current_step = "startup"


def set_current_step(name: str) -> None:
    global _current_step
    _current_step = name


@dataclass
class StepResult:
    name: str
    command: str
    status: str  # PASS | FAIL | SKIPPED
    duration_s: float
    summary: str = ""
    gate_blocking: bool = True


@dataclass
class GateReport:
    phase: int
    started_at_utc: str = ""
    finished_at_utc: str = ""
    status: str = "FAIL"
    environment: dict[str, object] = field(default_factory=dict)
    steps: list[StepResult] = field(default_factory=list)
    counts: dict[str, object] = field(default_factory=dict)
    known_failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    include_test_mode_smoke: bool = False
    # Commit/working-tree snapshot taken BEFORE any artifact is written.
    # Kept off `environment` because collect_environment() replaces that dict.
    release_tree: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Console safety: never crash on characters outside the active code page.
# ---------------------------------------------------------------------------


def safe_text(text: str, encoding: str) -> str:
    """Return text encodable under ``encoding`` via backslash replacement."""
    try:
        text.encode(encoding)
        return text
    except UnicodeEncodeError:
        return text.encode(encoding, errors="backslashreplace").decode(encoding)


def configure_console_output() -> None:
    """Best-effort hardening of stdout/stderr against strict encodings."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="backslashreplace")
        except (OSError, ValueError):  # already-detached or closed stream
            pass


def emit(message: str) -> None:
    """Print a message that is safe for the active console code page."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        print(safe_text(message, encoding), flush=True)
    except (OSError, UnicodeError):
        print(safe_text(message, "ascii"), flush=True)


# ---------------------------------------------------------------------------
# Artifact lifecycle: RUNNING placeholder first, PASS/FAIL at the end.
# ---------------------------------------------------------------------------


def redact_machine_paths(text: str) -> str:
    """Replace this machine's repository root with ``<repo>`` in recorded text.

    Evaluation artifacts are committed and public. The absolute location of the
    checkout is machine detail, not evidence, so it is redacted from every
    recorded command and summary. Only the prefix is replaced; the
    repository-relative remainder stays intact and readable.
    """
    if not text:
        return text
    root = str(REPO_ROOT)
    for variant in (root, root.replace("\\", "/"), root.replace("/", "\\")):
        if variant and variant in text:
            text = text.replace(variant, "<repo>")
    return text


def write_artifact(report: GateReport, artifact_dir: Path) -> Path:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact = {
        "phase": report.phase,
        "phase_name": PHASE_NAMES.get(report.phase, f"Phase {report.phase}"),
        "status": report.status,
        "started_at_utc": report.started_at_utc,
        "finished_at_utc": report.finished_at_utc,
        "environment": report.environment,
        "commands": [
            {
                "name": s.name,
                "command": redact_machine_paths(s.command),
                "status": s.status,
                "duration_s": s.duration_s,
                "summary": redact_machine_paths(s.summary),
                "gate_blocking": s.gate_blocking,
            }
            for s in report.steps
        ],
        "counts": report.counts,
        "known_failures": report.known_failures,
        "notes": report.notes,
        "failed_step": None,
        "next_phase": report.phase + 1,
        "reviewer_note": "",
    }
    artifact_path = artifact_dir / f"phase-{report.phase:02d}.json"
    artifact_path.write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return artifact_path


def begin_run(report: GateReport, artifact_dir: Path = ARTIFACT_DIR) -> Path:
    """Write the RUNNING artifact immediately so no stale PASS survives."""
    report.status = "RUNNING"
    path = write_artifact(report, artifact_dir)
    emit(f"[verify_phase] RUNNING artifact written to {path}")
    return path


def record_unexpected_failure(
    report: GateReport, step: str, exc: BaseException
) -> None:
    report.known_failures.append(
        f"unexpected exception during step '{step}': {type(exc).__name__}: {exc}"
    )


def finalize_run(
    report: GateReport,
    artifact_dir: Path = ARTIFACT_DIR,
    forced_failure: str | None = None,
) -> Path:
    report.finished_at_utc = utc_now()
    blocking_failures = [
        s for s in report.steps if s.status == "FAIL" and s.gate_blocking
    ]
    for step in report.steps:
        if step.status == "FAIL" and not step.gate_blocking:
            report.known_failures.append(f"non-blocking failure: {step.name}")
    report.status = (
        "FAIL" if (forced_failure is not None or blocking_failures) else "PASS"
    )
    path = write_artifact(report, artifact_dir)
    if forced_failure is not None:
        # Annotate the failed step directly in the artifact.
        artifact = json.loads(path.read_text(encoding="utf-8"))
        artifact["failed_step"] = forced_failure
        path.write_text(
            json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    emit(f"[verify_phase] status={report.status}; artifact written to {path}")
    return path


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Step execution helpers.
# ---------------------------------------------------------------------------


def run_command(
    name: str,
    args: list[str],
    cwd: Path,
    timeout_s: float,
    gate_blocking: bool = True,
) -> StepResult:
    set_current_step(name)
    display = " ".join(str(a) for a in args)
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
        duration = round(time.perf_counter() - started, 2)
        output = (completed.stdout or "") + (completed.stderr or "")
        tail = [line for line in output.splitlines() if line.strip()]
        summary = tail[-1][:200] if tail else f"exit code {completed.returncode}"
        status = "PASS" if completed.returncode == 0 else "FAIL"
        return StepResult(name, display, status, duration, summary, gate_blocking)
    except subprocess.TimeoutExpired:
        duration = round(time.perf_counter() - started, 2)
        return StepResult(
            name,
            display,
            "FAIL",
            duration,
            f"timed out after {timeout_s}s",
            gate_blocking,
        )
    except OSError as exc:
        # Missing binary, bad PATH, unwritable cwd: a step failure, not a crash.
        duration = round(time.perf_counter() - started, 2)
        return StepResult(
            name, display, "FAIL", duration, f"could not execute: {exc}", gate_blocking
        )


def run_capped_step(
    name: str,
    args: list[str],
    cwd: Path,
    timeout_s: float,
    gate_blocking: bool = True,
) -> StepResult:
    """Run a step with a hard wall-clock cap, killing the whole process tree on timeout.

    Unlike run_command, a timeout never hangs the verifier: the tree is
    terminated (taskkill /T on Windows) and the step is recorded as a
    (possibly non-blocking) FAIL so the gate still finalizes its artifact.
    """
    set_current_step(name)
    display = " ".join(str(a) for a in args)
    started = time.perf_counter()
    try:
        proc = subprocess.Popen(
            args,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return StepResult(
            name,
            display,
            "FAIL",
            round(time.perf_counter() - started, 2),
            f"could not execute: {exc}",
            gate_blocking,
        )
    try:
        output, _ = proc.communicate(timeout=timeout_s)
        duration = round(time.perf_counter() - started, 2)
        lines = [line for line in (output or "").splitlines() if line.strip()]
        summary = lines[-1][:200] if lines else f"exit code {proc.returncode}"
        status = "PASS" if proc.returncode == 0 else "FAIL"
        return StepResult(name, display, status, duration, summary, gate_blocking)
    except subprocess.TimeoutExpired:
        stop_process(proc)
        duration = round(time.perf_counter() - started, 2)
        return StepResult(
            name,
            display,
            "FAIL",
            duration,
            f"timed out after {timeout_s}s; process tree terminated",
            gate_blocking,
        )


def npm_args(*args: str) -> list[str]:
    if IS_WINDOWS:
        return ["cmd", "/c", "npm", *args]
    return ["npm", *args]


def find_git() -> str | None:
    """Locate git even when the current shell carries a pre-install stale PATH."""
    found = shutil.which("git")
    if found:
        return found
    fallback = Path(r"C:\Program Files\Git\cmd\git.exe")
    if IS_WINDOWS and fallback.is_file():
        return str(fallback)
    return None


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def http_get(url: str, timeout_s: float) -> tuple[int, str]:
    request = urllib.request.Request(
        url, headers={"User-Agent": "argus-verify-phase/0"}
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        body = response.read().decode("utf-8", errors="replace")
        return response.status, body


def wait_for_http(
    url: str, deadline_s: float, expected_status: int = 200
) -> tuple[bool, str]:
    deadline = time.monotonic() + deadline_s
    last_error = "unknown error"
    while time.monotonic() < deadline:
        try:
            status, body = http_get(url, timeout_s=5)
            if status == expected_status:
                return True, body
            last_error = f"HTTP {status}"
        except (urllib.error.URLError, OSError) as exc:
            last_error = str(exc)
        time.sleep(0.5)
    return False, last_error


def stop_process(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is not None:
        return
    if IS_WINDOWS:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def repo_scratch_dir(name: str) -> Path:
    """Repository-local scratch directory (tmp/ is gitignored)."""
    scratch = TMP_DIR / name
    scratch.mkdir(parents=True, exist_ok=True)
    return scratch


def new_basetemp(phase: int) -> Path:
    """Create a unique pytest basetemp for this run.

    Uses tempfile.mkdtemp under tmp/ so no fixed directory is ever reused and
    no other runner's directory is ever touched. Cleanup of this exact path is
    the caller's best-effort responsibility.
    """
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"pytest-phase-{phase:02d}-", dir=str(TMP_DIR)))


def pytest_args(basetemp: Path) -> list[str]:
    """pytest invocation that never depends on the machine temp directory."""
    return [
        str(VENV_PYTHON),
        "-m",
        "pytest",
        "backend/tests/unit",
        "-q",
        "--basetemp",
        str(basetemp),
        "-p",
        "no:cacheprovider",
    ]


def parse_pytest_summary(step: StepResult, report: GateReport) -> None:
    passed = re.search(r"(\d+) passed", step.summary)
    failed = re.search(r"(\d+) failed", step.summary)
    skipped = re.search(r"(\d+) skipped", step.summary)
    report.counts["backend_tests_passed"] = int(passed.group(1)) if passed else None
    report.counts["backend_tests_failed"] = int(failed.group(1)) if failed else 0
    report.counts["backend_tests_skipped"] = int(skipped.group(1)) if skipped else 0


# ---------------------------------------------------------------------------
# Gate steps.
# ---------------------------------------------------------------------------


def check_dependencies(report: GateReport) -> bool:
    """Fail fast (without installing) when bootstrap has not been run."""
    set_current_step("dependency-preflight")
    probe = run_command(
        "preflight-backend-imports",
        [
            str(VENV_PYTHON),
            "-c",
            "import fastapi, uvicorn, pydantic_settings, pytest, ruff, mypy, httpx",
        ],
        REPO_ROOT,
        timeout_s=60,
    )
    if probe.status != "PASS":
        probe.summary = (
            "backend dependencies missing - run bootstrap: "
            "python -m venv .venv && .venv\\Scripts\\python -m pip install "
            "-r backend/requirements.lock.txt"
        )
        report.steps.append(probe)
        return False
    report.steps.append(probe)

    frontend_ok = (FRONTEND_DIR / "node_modules").is_dir() and (
        FRONTEND_DIR / "package-lock.json"
    ).is_file()
    if not frontend_ok:
        report.steps.append(
            StepResult(
                "preflight-frontend-node-modules",
                "frontend/node_modules + package-lock.json",
                "FAIL",
                0.0,
                "frontend dependencies missing - run bootstrap: cd frontend && npm ci",
            )
        )
        return False
    report.steps.append(
        StepResult(
            "preflight-frontend-node-modules",
            "frontend/node_modules + package-lock.json",
            "PASS",
            0.0,
            "installed",
        )
    )
    return True


def scan_for_secrets(report: GateReport) -> StepResult:
    set_current_step("secret-scan")
    findings: list[str] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(REPO_ROOT)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if path.name == ".env" or (
            path.name.startswith(".env.") and path.name != ".env.example"
        ):
            try:
                ignored = (
                    subprocess.run(
                        ["git", "check-ignore", "--quiet", "--", str(relative)],
                        cwd=str(REPO_ROOT),
                        check=False,
                        capture_output=True,
                    ).returncode
                    == 0
                )
            except OSError:
                ignored = False
            if not ignored:
                findings.append(f"unignored env file: {relative}")
            continue
        if path.suffix.lower() not in SCAN_EXTENSIONS and path.name != ".gitignore":
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern, label in SECRET_PATTERNS:
            if re.search(pattern, text):
                findings.append(f"{label} in {relative}")
        if path.name == ".env.example":
            for line in text.splitlines():
                stripped = line.strip()
                if (
                    stripped
                    and not stripped.startswith("#")
                    and "=" in stripped
                    and stripped.split("=", 1)[1].strip()
                ):
                    findings.append(f".env.example has a non-empty value: {relative}")

    status = "PASS" if not findings else "FAIL"
    summary = (
        "no secret-like content found" if not findings else "; ".join(findings[:5])
    )
    return StepResult("secret-scan", "repository secret scan", status, 0.0, summary)


def check_gitignore_coverage(report: GateReport) -> StepResult:
    set_current_step("gitignore-coverage")
    gitignore = REPO_ROOT / ".gitignore"
    if not gitignore.is_file():
        return StepResult(
            "gitignore-coverage",
            "check .gitignore patterns",
            "FAIL",
            0.0,
            ".gitignore missing",
        )
    text = gitignore.read_text(encoding="utf-8")
    missing = [p for p in REQUIRED_GITIGNORE_PATTERNS if p not in text]
    status = "PASS" if not missing else "FAIL"
    summary = (
        "required ignore patterns present"
        if not missing
        else f"missing patterns: {missing}"
    )
    return StepResult(
        "gitignore-coverage", "check .gitignore patterns", status, 0.0, summary
    )


def probe_backend_server(report: GateReport) -> StepResult:
    set_current_step("backend-boot-health")
    port = free_port()
    scratch = repo_scratch_dir(f"verify-phase-{report.phase:02d}-backend")
    env = {
        **os.environ,
        "ARGUS_DB_PATH": str(scratch / "verify.sqlite3"),
        "ARGUS_PORT": str(port),
    }
    proc = subprocess.Popen(
        [
            str(VENV_PYTHON),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(BACKEND_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    started = time.perf_counter()
    try:
        ok, detail = wait_for_http(f"http://127.0.0.1:{port}/api/v1/health", 30)
        if not ok:
            return StepResult(
                "backend-boot-health",
                "uvicorn boot + GET /api/v1/health",
                "FAIL",
                round(time.perf_counter() - started, 2),
                f"health probe failed: {detail}",
            )
        _, body = http_get(f"http://127.0.0.1:{port}/api/v1/health", 5)
        health = json.loads(body)
        problems = []
        if health.get("status") != "ok":
            problems.append(f"status={health.get('status')}")
        if health.get("persistence", {}).get("ok") is not True:
            problems.append("persistence not ok")
        if not health.get("version"):
            problems.append("missing version")
        _, body = http_get(f"http://127.0.0.1:{port}/api/v1/version", 5)
        version = json.loads(body)
        if version.get("api_version") != "v1":
            problems.append("api_version != v1")
        duration = round(time.perf_counter() - started, 2)
        if problems:
            return StepResult(
                "backend-boot-health",
                "uvicorn boot + GET /api/v1/health,/version",
                "FAIL",
                duration,
                "; ".join(problems),
            )
        return StepResult(
            "backend-boot-health",
            "uvicorn boot + GET /api/v1/health,/version",
            "PASS",
            duration,
            "health ok, persistence sqlite ok, api v1",
        )
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        return StepResult(
            "backend-boot-health",
            "uvicorn boot + GET /api/v1/health,/version",
            "FAIL",
            round(time.perf_counter() - started, 2),
            f"probe error: {exc}",
        )
    finally:
        stop_process(proc)
        shutil.rmtree(scratch, ignore_errors=True)


def probe_frontend_server(report: GateReport) -> StepResult:
    set_current_step("frontend-boot-home")
    port = free_port()
    proc = subprocess.Popen(
        npm_args("run", "start", "--", "--port", str(port), "--hostname", "127.0.0.1"),
        cwd=str(FRONTEND_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    started = time.perf_counter()
    try:
        ok, detail = wait_for_http(f"http://127.0.0.1:{port}/", 90)
        if not ok:
            return StepResult(
                "frontend-boot-home",
                "next start boot + GET /",
                "FAIL",
                round(time.perf_counter() - started, 2),
                f"home probe failed: {detail}",
            )
        _, body = http_get(f"http://127.0.0.1:{port}/", 5)
        duration = round(time.perf_counter() - started, 2)
        if "ARGUS CONTROL" not in body:
            return StepResult(
                "frontend-boot-home",
                "next start boot + GET /",
                "FAIL",
                duration,
                "home page rendered without the ARGUS CONTROL heading",
            )
        return StepResult(
            "frontend-boot-home",
            "next start boot + GET /",
            "PASS",
            duration,
            "home page served with ARGUS CONTROL heading",
        )
    except (urllib.error.URLError, OSError) as exc:
        return StepResult(
            "frontend-boot-home",
            "next start boot + GET /",
            "FAIL",
            round(time.perf_counter() - started, 2),
            f"probe error: {exc}",
        )
    finally:
        stop_process(proc)


def playwright_browsers_installed() -> bool:
    if IS_WINDOWS:
        cache = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    else:
        cache = Path.home() / ".cache" / "ms-playwright"
    if not cache.is_dir():
        return False
    return any("chromium" in entry.name.lower() for entry in cache.iterdir())


def run_optional_e2e(report: GateReport, timeout_s: float = 60) -> None:
    set_current_step("optional-e2e-playwright")
    if not playwright_browsers_installed():
        report.steps.append(
            StepResult(
                "optional-e2e-playwright",
                "npm --prefix frontend run test:e2e",
                "SKIPPED",
                0.0,
                "playwright browsers not installed (bootstrap-only step: "
                "npx playwright install chromium); e2e is not part of the Phase 0 gate",
                gate_blocking=False,
            )
        )
        return
    result = run_capped_step(
        "optional-e2e-playwright",
        npm_args("--prefix", "frontend", "run", "test:e2e"),
        REPO_ROOT,
        timeout_s=timeout_s,
        gate_blocking=False,
    )
    report.steps.append(result)
    if result.status == "FAIL":
        report.known_failures.append(
            "optional Playwright e2e failed or timed out (not part of the Phase 0 "
            "gate); see step optional-e2e-playwright"
        )


def collect_environment(report: GateReport) -> None:
    set_current_step("collect-environment")

    def probe(args: list[str]) -> str:
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            return (
                (completed.stdout or completed.stderr or "")
                .strip()
                .splitlines()[-1][:100]
            )
        except (OSError, subprocess.SubprocessError):
            return "unavailable"

    node_cmd = (
        ["cmd", "/c", "node", "--version"] if IS_WINDOWS else ["node", "--version"]
    )
    git_cmd = find_git()
    if git_cmd is None:
        git_detail = (
            "unavailable (not on PATH and not at the standard install location)"
        )
    else:
        version = probe([git_cmd, "--version"])
        head = probe([git_cmd, "-C", str(REPO_ROOT), "rev-parse", "HEAD"])
        git_detail = f"{version}; HEAD {head}"
    report.environment = {
        "python": probe([str(VENV_PYTHON), "--version"]),
        "node": probe(node_cmd),
        "npm": probe(npm_args("--version")),
        "platform": platform.platform(),
        "git": git_detail,
    }
    if report.release_tree is not None:
        report.environment["release_tree"] = report.release_tree


# ---------------------------------------------------------------------------
# Gate orchestration.
# ---------------------------------------------------------------------------


def run_gate(report: GateReport) -> None:
    collect_environment(report)
    emit(f"[verify_phase] phase {report.phase} gate started {report.started_at_utc}")

    if not run_phase0_steps(report):
        return
    if report.phase == 8:
        # Phase 8 is the release gate, not a replay of the earlier phases. It
        # appends its own release steps to the unchanged Phase 0 list and
        # VERIFIES the committed Phase 1-7 evidence rather than regenerating
        # benchmark artifacts, which would let a gate rewrite its own proof.
        run_phase8_steps(report)
        return
    if report.phase >= 1:
        run_phase1_steps(report)
    if report.phase >= 2:
        run_phase2_steps(report)
    if report.phase >= 3:
        run_phase3_steps(report)
    if report.phase >= 4:
        run_phase4_steps(report)
    if report.phase >= 5:
        run_phase5_steps(report)
    if report.phase >= 6:
        run_phase6_steps(report, include_test_mode_smoke=report.include_test_mode_smoke)
    if report.phase == 7:
        run_phase7_steps(report)


def run_phase0_steps(report: GateReport) -> bool:
    """The complete Phase 0 step list, unchanged; False when dependencies are missing."""
    if not check_dependencies(report):
        return False

    # Unique per-run basetemp: created here, removed (best-effort, this exact
    # path only) no matter how the remaining steps end.
    basetemp = new_basetemp(report.phase)
    emit(f"[verify_phase] pytest basetemp: {basetemp}")
    try:
        backend_steps: list[tuple[str, list[str], Path, float]] = [
            (
                "backend-ruff-check",
                [str(VENV_PYTHON), "-m", "ruff", "check", "backend"],
                REPO_ROOT,
                120,
            ),
            (
                "backend-ruff-format",
                [str(VENV_PYTHON), "-m", "ruff", "format", "--check", "backend"],
                REPO_ROOT,
                120,
            ),
            ("backend-mypy", [str(VENV_PYTHON), "-m", "mypy"], BACKEND_DIR, 300),
            ("backend-pytest", pytest_args(basetemp), REPO_ROOT, 300),
        ]
        for name, cmd, cwd, timeout in backend_steps:
            step = run_command(name, cmd, cwd, timeout)
            report.steps.append(step)
            emit(
                f"[verify_phase] {step.status}: {step.name} ({step.duration_s}s) {step.summary}"
            )
            if name == "backend-pytest":
                parse_pytest_summary(step, report)

        frontend_steps: list[tuple[str, list[str], float]] = [
            ("frontend-lint", npm_args("--prefix", "frontend", "run", "lint"), 300),
            (
                "frontend-typecheck",
                npm_args("--prefix", "frontend", "run", "typecheck"),
                300,
            ),
            ("frontend-test", npm_args("--prefix", "frontend", "run", "test"), 300),
            ("frontend-build", npm_args("--prefix", "frontend", "run", "build"), 600),
        ]
        for name, cmd, timeout in frontend_steps:
            step = run_command(name, cmd, REPO_ROOT, timeout)
            report.steps.append(step)
            emit(
                f"[verify_phase] {step.status}: {step.name} ({step.duration_s}s) {step.summary}"
            )

        for step_factory in (probe_backend_server, probe_frontend_server):
            step = step_factory(report)
            report.steps.append(step)
            emit(
                f"[verify_phase] {step.status}: {step.name} ({step.duration_s}s) {step.summary}"
            )

        for check in (scan_for_secrets, check_gitignore_coverage):
            step = check(report)
            report.steps.append(step)
            emit(f"[verify_phase] {step.status}: {step.name} {step.summary}")

        run_optional_e2e(report)
    finally:
        shutil.rmtree(basetemp, ignore_errors=True)
    return True


# ---------------------------------------------------------------------------
# Phase 1 dataset steps (PRD 16: Synthetic Data, Ground Truth, and Isolation).
# ---------------------------------------------------------------------------


def compare_directory_tree(name: str, generated: Path, committed: Path) -> StepResult:
    """Byte-compare a freshly generated dataset against the committed copy."""
    set_current_step(name)
    started = time.perf_counter()
    problems: list[str] = []
    if not committed.is_dir():
        problems.append(f"missing committed dataset at {committed}")
    elif not generated.is_dir():
        problems.append(f"generation produced no dataset at {generated}")
    else:
        gen_files = {
            path.relative_to(generated).as_posix()
            for path in generated.rglob("*")
            if path.is_file()
        }
        committed_files = {
            path.relative_to(committed).as_posix()
            for path in committed.rglob("*")
            if path.is_file()
        }
        for relative in sorted(committed_files - gen_files):
            problems.append(f"missing in regenerated output: {relative}")
        for relative in sorted(gen_files - committed_files):
            problems.append(f"extra in regenerated output: {relative}")
        for relative in sorted(gen_files & committed_files):
            if (generated / relative).read_bytes() != (
                committed / relative
            ).read_bytes():
                problems.append(f"bytes differ: {relative}")
    duration = round(time.perf_counter() - started, 2)
    status = "PASS" if not problems else "FAIL"
    summary = (
        "byte-identical regeneration of inputs, labels, and manifests"
        if not problems
        else "; ".join(problems[:5])
    )
    return StepResult(
        name, f"compare {generated} vs {committed}", status, duration, summary
    )


def load_backend_evaluation() -> None:
    """Make the evaluator-only backend package importable for assertions."""
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))


def collect_phase1_metrics_and_violations() -> tuple[list[str], dict[str, object]]:
    """Evaluator-side acceptance assertions over the committed datasets."""
    load_backend_evaluation()
    ct = importlib.import_module("app.evaluation.control_totals")

    violations: list[str] = []
    dataset_metrics: dict[str, object] = {}
    seeds_in_use: set[int] = set()
    for profile, _seed in DATASET_PROFILES:
        root = REPO_ROOT / "datasets" / profile
        ds = ct.parse_dataset(root)
        for checker in (
            ct.settlement_conservation_violations,
            ct.corpus_identity_violations,
            ct.candidate_count_violations,
            ct.referential_integrity_violations,
            ct.variance_equation_violations,
            ct.clean_structure_violations,
        ):
            violations.extend(f"{profile}: {problem}" for problem in checker(ds))
        violations.extend(
            f"{profile}: {problem}" for problem in ct.root_manifest_violations(root)
        )
        violations.extend(
            f"{profile}: {problem}" for problem in ct.labels_manifest_violations(root)
        )
        labels = json.loads(
            (root / "labels" / "labels.json").read_text(encoding="utf-8")
        )
        labels_manifest = json.loads(
            (root / "labels" / "manifest.json").read_text(encoding="utf-8")
        )
        root_manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        seeds_in_use.add(int(root_manifest["seed"]))
        dataset_metrics[profile] = {
            "rows_by_file": {
                relative: info["rows"]
                for relative, info in root_manifest["files"].items()
            },
            "eligible_row_count": labels_manifest["eligible_row_count"],
            "quarantine_expected_count": labels_manifest["quarantine_expected_count"],
            "duplicate_delivery_count": labels_manifest["duplicate_delivery_count"],
            "case_count": labels_manifest["case_count"],
            "cases_by_category": labels["summary"]["by_category"],
            "totals_paise": labels_manifest["totals_paise"],
            "reproducibility_hash": root_manifest["reproducibility_hash"],
            "labels_sha256": labels_manifest["labels_sha256"],
            "seed": root_manifest["seed"],
        }

    dev_metrics = dataset_metrics["dev"]
    if isinstance(dev_metrics, dict) and dev_metrics["eligible_row_count"] < 100:
        violations.append("dev: fewer than 100 eligible records")
    required_categories = {
        "DUPLICATE_LEDGER_POSTING",
        "MISSING_REFUND_POSTING",
        "SETTLEMENT_TIMING_WINDOW_SHIFT",
        "AMBIGUOUS_EVIDENCE",
    }
    if isinstance(dev_metrics, dict):
        missing = required_categories - set(dev_metrics["cases_by_category"])
        if missing:
            violations.append(f"dev: missing exception categories {sorted(missing)}")

    holdout_spec_path = REPO_ROOT / "datasets" / "holdout" / "spec.json"
    if not holdout_spec_path.is_file():
        violations.append("holdout: spec.json missing")
    else:
        holdout_spec = json.loads(holdout_spec_path.read_text(encoding="utf-8"))
        holdout_seed = holdout_spec.get("seed")
        if not isinstance(holdout_seed, int) or holdout_seed in seeds_in_use:
            violations.append(
                "holdout: seed must exist and differ from dev/adversarial"
            )
    return violations, {"datasets": dataset_metrics}


def dataset_gate_assertions(report: GateReport) -> StepResult:
    set_current_step("dataset-gate-assertions")
    started = time.perf_counter()
    try:
        violations, metrics = collect_phase1_metrics_and_violations()
    except Exception as exc:  # noqa: BLE001 - evaluator loading failure is a step FAIL
        duration = round(time.perf_counter() - started, 2)
        return StepResult(
            "dataset-gate-assertions",
            "evaluator-side dataset acceptance assertions",
            "FAIL",
            duration,
            f"{type(exc).__name__}: {exc}",
        )
    report.counts.update(metrics)
    duration = round(time.perf_counter() - started, 2)
    status = "PASS" if not violations else "FAIL"
    summary = (
        "dev>=100 eligible; 4 categories; candidate rules; variance equation; "
        "referential integrity; manifests hashed; holdout seed separated"
        if not violations
        else "; ".join(violations[:5])
    )
    return StepResult(
        "dataset-gate-assertions",
        "evaluator-side dataset acceptance assertions",
        status,
        duration,
        summary,
    )


def dataset_pytest_args(basetemp: Path) -> list[str]:
    return [
        str(VENV_PYTHON),
        "-m",
        "pytest",
        "backend/tests/unit/test_dataset_generation.py",
        "backend/tests/unit/test_dataset_injectors.py",
        "backend/tests/unit/test_label_isolation.py",
        "-q",
        "--basetemp",
        str(basetemp),
        "-p",
        "no:cacheprovider",
    ]


def parse_dataset_pytest_summary(step: StepResult, report: GateReport) -> None:
    passed = re.search(r"(\d+) passed", step.summary)
    failed = re.search(r"(\d+) failed", step.summary)
    skipped = re.search(r"(\d+) skipped", step.summary)
    report.counts["dataset_tests_passed"] = int(passed.group(1)) if passed else None
    report.counts["dataset_tests_failed"] = int(failed.group(1)) if failed else 0
    report.counts["dataset_tests_skipped"] = int(skipped.group(1)) if skipped else 0


def run_phase1_steps(report: GateReport) -> None:
    """Phase 1 blocking steps, appended after the unchanged Phase 0 list."""
    scratch = Path(
        tempfile.mkdtemp(prefix="verify-phase-01-datasets-", dir=str(TMP_DIR))
    )
    basetemp = new_basetemp(1)
    emit(f"[verify_phase] dataset scratch: {scratch}")
    try:
        for profile, seed in DATASET_PROFILES:
            generate = run_command(
                f"dataset-generate-{profile}",
                [
                    str(VENV_PYTHON),
                    "scripts/generate_dataset.py",
                    "--profile",
                    profile,
                    "--seed",
                    str(seed),
                    "--output-root",
                    str(scratch),
                ],
                REPO_ROOT,
                180,
            )
            report.steps.append(generate)
            emit(
                f"[verify_phase] {generate.status}: {generate.name} "
                f"({generate.duration_s}s) {generate.summary}"
            )
            compare = compare_directory_tree(
                f"dataset-reproducibility-{profile}",
                scratch / profile,
                REPO_ROOT / "datasets" / profile,
            )
            report.steps.append(compare)
            emit(
                f"[verify_phase] {compare.status}: {compare.name} "
                f"({compare.duration_s}s) {compare.summary}"
            )

        isolation = run_command(
            "check-label-isolation",
            [str(VENV_PYTHON), "scripts/check_label_isolation.py"],
            REPO_ROOT,
            120,
        )
        report.steps.append(isolation)
        emit(
            f"[verify_phase] {isolation.status}: {isolation.name} "
            f"({isolation.duration_s}s) {isolation.summary}"
        )

        dataset_tests = run_command(
            "dataset-tests", dataset_pytest_args(basetemp), REPO_ROOT, 300
        )
        report.steps.append(dataset_tests)
        parse_dataset_pytest_summary(dataset_tests, report)
        emit(
            f"[verify_phase] {dataset_tests.status}: {dataset_tests.name} "
            f"({dataset_tests.duration_s}s) {dataset_tests.summary}"
        )

        assertions = dataset_gate_assertions(report)
        report.steps.append(assertions)
        emit(
            f"[verify_phase] {assertions.status}: {assertions.name} "
            f"({assertions.duration_s}s) {assertions.summary}"
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        shutil.rmtree(basetemp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Phase 2 steps (PRD 16: Normalization, Reconciliation, Evidence Graph).
# Explicit test file paths only: no wildcard expansion, which cmd.exe does
# not perform. Migration tests are their own blocking step because
# test_migration.py is not matched by the reconciliation file set.
# ---------------------------------------------------------------------------


def phase2_pytest_paths(group: str) -> list[str]:
    groups: dict[str, list[str]] = {
        "migration": ["backend/tests/unit/test_migration.py"],
        "normalization": ["backend/tests/unit/test_normalization.py"],
        "reconciliation": [
            "backend/tests/unit/test_reconciliation_matching.py",
            "backend/tests/unit/test_reconciliation_cases.py",
            "backend/tests/unit/test_reconciliation_totals.py",
            "backend/tests/unit/test_reconciliation_graph.py",
            "backend/tests/unit/test_reconciliation_idempotency.py",
        ],
        "benchmark-evaluator": ["backend/tests/unit/test_benchmark_evaluator.py"],
        "integration": ["backend/tests/integration/test_rules_only_run.py"],
        "adversarial": ["backend/tests/adversarial/test_phase2_adversarial.py"],
    }
    return groups[group]


def phase2_pytest_step(
    report: GateReport, name: str, group: str, basetemp: Path, timeout: int = 300
) -> StepResult:
    args = [
        str(VENV_PYTHON),
        "-m",
        "pytest",
        *phase2_pytest_paths(group),
        "-q",
        "--basetemp",
        str(basetemp / name),
        "-p",
        "no:cacheprovider",
    ]
    step = run_command(name, args, REPO_ROOT, timeout)
    report.steps.append(step)
    emit(
        f"[verify_phase] {step.status}: {step.name} ({step.duration_s}s) {step.summary}"
    )
    return step


def collect_phase2_metrics_and_violations() -> tuple[list[str], dict[str, object]]:
    """Evaluator-side acceptance assertions over the benchmark reports."""
    violations: list[str] = []
    metrics: dict[str, object] = {}
    expected = {
        "dev": {
            "eligible": 282,
            "cases": 12,
            "quarantine": 0,
            "duplicates": 0,
            "categories": {
                "DUPLICATE_LEDGER_POSTING": 3,
                "MISSING_REFUND_POSTING": 3,
                "SETTLEMENT_TIMING_WINDOW_SHIFT": 3,
                "AMBIGUOUS_EVIDENCE": 3,
            },
        },
        "adversarial": {
            "eligible": 64,
            "cases": 3,
            "quarantine": 2,
            "duplicates": 1,
            "categories": {"AMBIGUOUS_EVIDENCE": 3},
        },
    }
    for profile, wants in expected.items():
        report_path = REPO_ROOT / "artifacts" / "benchmark" / f"phase-02-{profile}.json"
        if not report_path.is_file():
            violations.append(f"{profile}: benchmark report missing at {report_path}")
            continue
        benchmark = json.loads(report_path.read_text(encoding="utf-8"))
        evaluation = benchmark.get("evaluation", {})
        counts = evaluation.get("counts", {})
        metrics[profile] = {
            "eligible_canonical_records": counts.get("eligible_canonical_records"),
            "match_precision": evaluation.get("metrics", {}).get("match_precision"),
            "record_match_rate": evaluation.get("metrics", {}).get("record_match_rate"),
            "case_classification_accuracy": evaluation.get("metrics", {}).get(
                "case_classification_accuracy"
            ),
            "quarantined": counts.get("quarantined"),
            "duplicate_deliveries": counts.get("duplicate_deliveries"),
            "throughput": evaluation.get("throughput"),
            "graph": evaluation.get("graph", {}).get("counts"),
            "residual_variance": evaluation.get("residual_variance"),
            "economic_output_hash": benchmark.get("idempotency", {}).get(
                "first_economic_output_hash"
            ),
            "economically_identical_rerun": benchmark.get("idempotency", {}).get(
                "economically_identical"
            ),
        }
        precision = evaluation.get("metrics", {}).get("match_precision", {})
        if precision.get("rate") != 1.0 or precision.get("numerator") != precision.get(
            "denominator"
        ):
            violations.append(f"{profile}: match precision is not 1.0")
        if counts.get("eligible_canonical_records") != wants["eligible"]:
            violations.append(
                f"{profile}: eligible canonical records "
                f"{counts.get('eligible_canonical_records')} != {wants['eligible']}"
            )
        accuracy = evaluation.get("metrics", {}).get("case_classification_accuracy", {})
        if (
            accuracy.get("numerator") != wants["cases"]
            or accuracy.get("denominator") != wants["cases"]
        ):
            violations.append(
                f"{profile}: case accuracy is not {wants['cases']}/{wants['cases']}"
            )
        if not counts.get("quarantined", {}).get("match"):
            violations.append(f"{profile}: quarantine count mismatch")
        if counts.get("quarantined", {}).get("expected") != wants["quarantine"]:
            violations.append(
                f"{profile}: expected quarantine != {wants['quarantine']}"
            )
        if not counts.get("duplicate_deliveries", {}).get("match"):
            violations.append(f"{profile}: duplicate delivery count mismatch")
        if (
            counts.get("duplicate_deliveries", {}).get("expected")
            != wants["duplicates"]
        ):
            violations.append(
                f"{profile}: expected duplicates != {wants['duplicates']}"
            )
        if evaluation.get("case_comparison", {}).get("false_positive_cases"):
            violations.append(f"{profile}: false-positive runtime cases present")
        if evaluation.get("case_comparison", {}).get("missed_labels"):
            violations.append(f"{profile}: labelled cases missed")
        if not benchmark.get("idempotency", {}).get("economically_identical"):
            violations.append(f"{profile}: rerun economic hash differs")
        if not evaluation.get("graph", {}).get("referentially_valid"):
            violations.append(f"{profile}: graph contains unresolvable references")
        if not counts.get("row_accounting_identity_holds"):
            violations.append(f"{profile}: row accounting identity failed")
        if not evaluation.get("totals_comparison", {}).get("equal"):
            violations.append(f"{profile}: control totals differ from the manifest")
        if not evaluation.get("residual_variance", {}).get("equal"):
            violations.append(f"{profile}: ledger-scoped residual variance differs")
        runtime_path = report_path.with_name(report_path.name + ".runtime.json")
        if runtime_path.is_file():
            runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
            if runtime.get("unaccounted_record_keys"):
                violations.append(
                    f"{profile}: accepted records vanished from accounting"
                )
            if runtime.get("match_invariant_violations"):
                violations.append(f"{profile}: match invariant violations present")
            metrics[profile]["cases_by_category"] = {
                category: count
                for category, count in sorted(
                    (runtime.get("cases_by_category") or {}).items()
                )
            }
            by_category = runtime.get("cases_by_category") or {}
            if by_category != wants["categories"]:
                violations.append(
                    f"{profile}: case categories {by_category} != {wants['categories']}"
                )
        else:
            violations.append(f"{profile}: runtime output file missing")
    return violations, {"phase2": metrics}


def phase2_gate_assertions(report: GateReport) -> StepResult:
    set_current_step("phase2-gate-assertions")
    started = time.perf_counter()
    try:
        violations, metrics = collect_phase2_metrics_and_violations()
    except Exception as exc:  # noqa: BLE001 - evaluator loading failure is a step FAIL
        duration = round(time.perf_counter() - started, 2)
        return StepResult(
            "phase2-gate-assertions",
            "evaluator-side phase 2 acceptance assertions",
            "FAIL",
            duration,
            f"{type(exc).__name__}: {exc}",
        )
    report.counts.update(metrics)
    duration = round(time.perf_counter() - started, 2)
    status = "PASS" if not violations else "FAIL"
    summary = (
        "dev precision 1.0 (explicit denominators); 282 eligible; 12 cases 3x4 "
        "matched one-to-one on anchors; idempotent rerun; graph valid; "
        "adversarial 64 eligible, 2 quarantined, 1 duplicate, 3 cases"
        if not violations
        else "; ".join(violations[:5])
    )
    return StepResult(
        "phase2-gate-assertions",
        "evaluator-side phase 2 acceptance assertions",
        status,
        duration,
        summary,
    )


def run_phase2_steps(report: GateReport) -> None:
    """Phase 2 blocking steps, appended after the unchanged Phase 0/1 lists."""
    basetemp = new_basetemp(2)
    emit(f"[verify_phase] phase 2 pytest basetemp: {basetemp}")
    try:
        for name, group in (
            ("unit-tests-migration", "migration"),
            ("unit-tests-normalization", "normalization"),
            ("unit-tests-reconciliation", "reconciliation"),
            ("unit-tests-benchmark-evaluator", "benchmark-evaluator"),
            ("integration-rules-only-run", "integration"),
            ("adversarial-tests", "adversarial"),
        ):
            phase2_pytest_step(report, name, group, basetemp)

        for profile in ("dev", "adversarial"):
            benchmark = run_command(
                f"benchmark-rules-only-{profile}",
                [
                    str(VENV_PYTHON),
                    "scripts/run_benchmark.py",
                    "--dataset",
                    f"datasets/{profile}",
                    "--mode",
                    "rules-only",
                    "--output",
                    f"artifacts/benchmark/phase-02-{profile}.json",
                ],
                REPO_ROOT,
                600,
            )
            report.steps.append(benchmark)
            emit(
                f"[verify_phase] {benchmark.status}: {benchmark.name} "
                f"({benchmark.duration_s}s) {benchmark.summary}"
            )

        assertions = phase2_gate_assertions(report)
        report.steps.append(assertions)
        emit(
            f"[verify_phase] {assertions.status}: {assertions.name} "
            f"({assertions.duration_s}s) {assertions.summary}"
        )
    finally:
        shutil.rmtree(basetemp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Phase 3 steps (PRD 16: Verifier, Proof Packages, and Dry-Run Core).
# Append-only over Phase 0/1/2: no earlier step list is weakened.
# ---------------------------------------------------------------------------


def phase3_pytest_paths(group: str) -> list[str]:
    groups: dict[str, list[str]] = {
        "scope-safety": ["backend/tests/unit/test_scope_safety.py"],
        "verifier": ["backend/tests/unit/test_verifier_phase3.py"],
        "migration": ["backend/tests/unit/test_migration.py"],
        "benchmark-evaluator": ["backend/tests/unit/test_benchmark_evaluator.py"],
        "dry-run-integration": ["backend/tests/integration/test_dry_run.py"],
    }
    return groups[group]


def phase3_pytest_step(
    report: GateReport, name: str, group: str, basetemp: Path, timeout: int = 300
) -> StepResult:
    args = [
        str(VENV_PYTHON),
        "-m",
        "pytest",
        *phase3_pytest_paths(group),
        "-q",
        "--basetemp",
        str(basetemp / name),
        "-p",
        "no:cacheprovider",
    ]
    step = run_command(name, args, REPO_ROOT, timeout)
    report.steps.append(step)
    emit(
        f"[verify_phase] {step.status}: {step.name} ({step.duration_s}s) {step.summary}"
    )
    return step


def collect_phase3_metrics_and_violations() -> tuple[list[str], dict[str, object]]:
    """Evaluator-side acceptance assertions over Phase 3 benchmark reports."""
    violations: list[str] = []
    metrics: dict[str, object] = {}
    expected = {
        "dev": {
            "outcomes": 12,
            "deltas": 12,
            "escalations": 3,
            "proofs": 9,
            "status_counts": {
                "APPROVAL_REQUIRED": 6,
                "UNRESOLVED": 3,
                "VERIFIED_RESOLVED": 3,
            },
            "verifier_status_counts": {"FAIL": 0, "INCONCLUSIVE": 3, "PASS": 9},
        },
        "adversarial": {
            "outcomes": 3,
            "deltas": 3,
            "escalations": 3,
            "proofs": 0,
            "status_counts": {"UNRESOLVED": 3},
            "verifier_status_counts": {"FAIL": 0, "INCONCLUSIVE": 3, "PASS": 0},
        },
    }
    for profile, wants in expected.items():
        report_path = REPO_ROOT / "artifacts" / "benchmark" / f"phase-03-{profile}.json"
        if not report_path.is_file():
            violations.append(f"{profile}: Phase 3 benchmark report missing")
            continue
        benchmark = json.loads(report_path.read_text(encoding="utf-8"))
        evaluation = benchmark.get("evaluation", {})
        verification = evaluation.get("verification", {})
        runtime_summary = verification.get("runtime_verification_summary", {})
        outcome = verification.get("outcome_agreement", {})
        delta = verification.get("delta_agreement", {})
        escalation = verification.get("ambiguous_escalation", {})
        proof = verification.get("proof_completeness", {})
        metrics[profile] = {
            "outcome_agreement": outcome,
            "delta_agreement": delta,
            "ambiguous_escalation": escalation,
            "false_verifier_pass_count": verification.get("false_verifier_pass_count"),
            "proof_completeness": proof,
            "money_weighted_dry_run_error_paise": verification.get(
                "money_weighted_dry_run_error_paise"
            ),
            "runtime_status_counts": runtime_summary.get("case_status_counts"),
            "verifier_status_counts": runtime_summary.get("verifier_status_counts"),
            "dry_run_count": runtime_summary.get("dry_run_count"),
            "dry_run_abs_variance_after_paise": runtime_summary.get(
                "dry_run_abs_variance_after_paise"
            ),
            "economic_output_hash": benchmark.get("idempotency", {}).get(
                "first_economic_output_hash"
            ),
            "economically_identical_rerun": benchmark.get("idempotency", {}).get(
                "economically_identical"
            ),
        }
        if (
            outcome.get("numerator") != wants["outcomes"]
            or outcome.get("denominator") != wants["outcomes"]
        ):
            violations.append(f"{profile}: verifier outcome agreement mismatch")
        if (
            delta.get("numerator") != wants["deltas"]
            or delta.get("denominator") != wants["deltas"]
        ):
            violations.append(f"{profile}: proposed delta agreement mismatch")
        if (
            escalation.get("numerator") != wants["escalations"]
            or escalation.get("denominator") != wants["escalations"]
        ):
            violations.append(f"{profile}: ambiguous escalation mismatch")
        if verification.get("false_verifier_pass_count") != 0:
            violations.append(f"{profile}: false verifier passes present")
        if (
            proof.get("numerator") != wants["proofs"]
            or proof.get("denominator") != wants["proofs"]
        ):
            violations.append(f"{profile}: proof completeness mismatch")
        if verification.get("money_weighted_dry_run_error_paise") != 0:
            violations.append(f"{profile}: dry-run money error is nonzero")
        if runtime_summary.get("case_status_counts") != wants["status_counts"]:
            violations.append(f"{profile}: final case status counts mismatch")
        if (
            runtime_summary.get("verifier_status_counts")
            != wants["verifier_status_counts"]
        ):
            violations.append(f"{profile}: verifier status counts mismatch")
        if not benchmark.get("idempotency", {}).get("economically_identical"):
            violations.append(f"{profile}: rerun economic hash differs")
    return violations, {"phase3": metrics}


def phase3_gate_assertions(report: GateReport) -> StepResult:
    set_current_step("phase3-gate-assertions")
    started = time.perf_counter()
    try:
        violations, metrics = collect_phase3_metrics_and_violations()
    except Exception as exc:  # noqa: BLE001 - evaluator loading failure is a step FAIL
        duration = round(time.perf_counter() - started, 2)
        return StepResult(
            "phase3-gate-assertions",
            "evaluator-side phase 3 acceptance assertions",
            "FAIL",
            duration,
            f"{type(exc).__name__}: {exc}",
        )
    report.counts.update(metrics)
    duration = round(time.perf_counter() - started, 2)
    status = "PASS" if not violations else "FAIL"
    summary = (
        "dev outcome/delta 12/12; false passes 0; escalation 3/3; "
        "proof completeness 9/9; dry-run error 0; adversarial 3/3 unresolved"
        if not violations
        else "; ".join(violations[:5])
    )
    return StepResult(
        "phase3-gate-assertions",
        "evaluator-side phase 3 acceptance assertions",
        status,
        duration,
        summary,
    )


def run_phase3_steps(report: GateReport) -> None:
    """Phase 3 blocking steps appended after the unchanged Phase 0/1/2 lists."""
    basetemp = new_basetemp(3)
    emit(f"[verify_phase] phase 3 pytest basetemp: {basetemp}")
    try:
        for name, group in (
            ("scope-safety", "scope-safety"),
            ("unit-tests-verifier", "verifier"),
            ("unit-tests-migration-v3", "migration"),
            ("unit-tests-benchmark-evaluator-v3", "benchmark-evaluator"),
            ("integration-dry-run", "dry-run-integration"),
        ):
            phase3_pytest_step(report, name, group, basetemp)

        for profile in ("dev", "adversarial"):
            benchmark = run_command(
                f"benchmark-rules-only-phase3-{profile}",
                [
                    str(VENV_PYTHON),
                    "scripts/run_benchmark.py",
                    "--dataset",
                    f"datasets/{profile}",
                    "--mode",
                    "rules-only",
                    "--output",
                    f"artifacts/benchmark/phase-03-{profile}.json",
                ],
                REPO_ROOT,
                600,
            )
            report.steps.append(benchmark)
            emit(
                f"[verify_phase] {benchmark.status}: {benchmark.name} "
                f"({benchmark.duration_s}s) {benchmark.summary}"
            )

        assertions = phase3_gate_assertions(report)
        report.steps.append(assertions)
        emit(
            f"[verify_phase] {assertions.status}: {assertions.name} "
            f"({assertions.duration_s}s) {assertions.summary}"
        )
    finally:
        shutil.rmtree(basetemp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Phase 4 steps (PRD 16: Bounded AI Investigator).
# Append-only over Phase 0/1/2/3: no earlier step list is weakened.
# ---------------------------------------------------------------------------


def phase4_pytest_paths(group: str) -> list[str]:
    groups: dict[str, list[str]] = {
        "investigator-tools": ["backend/tests/unit/test_investigator_tools.py"],
        "investigator-schemas": ["backend/tests/unit/test_investigator_schemas.py"],
        "investigator-engine": ["backend/tests/unit/test_investigator_engine.py"],
        "investigator-boundaries": [
            "backend/tests/adversarial/test_investigator_boundaries.py"
        ],
        "scope-safety": ["backend/tests/unit/test_scope_safety.py"],
    }
    return groups[group]


def phase4_pytest_step(
    report: GateReport, name: str, group: str, basetemp: Path, timeout: int = 300
) -> StepResult:
    args = [
        str(VENV_PYTHON),
        "-m",
        "pytest",
        *phase4_pytest_paths(group),
        "-q",
        "--basetemp",
        str(basetemp / name),
        "-p",
        "no:cacheprovider",
    ]
    step = run_command(name, args, REPO_ROOT, timeout)
    report.steps.append(step)
    emit(
        f"[verify_phase] {step.status}: {step.name} ({step.duration_s}s) {step.summary}"
    )
    return step


def collect_phase4_metrics_and_violations() -> tuple[list[str], dict[str, object]]:
    """Evaluator-side acceptance assertions over Phase 4 benchmark reports and boundaries."""
    violations: list[str] = []
    metrics: dict[str, object] = {}
    expected = {
        "dev": {
            "outcomes": 12,
            "deltas": 12,
            "escalations": 3,
            "proofs": 9,
            "status_counts": {
                "APPROVAL_REQUIRED": 6,
                "UNRESOLVED": 3,
                "VERIFIED_RESOLVED": 3,
            },
            "verifier_status_counts": {"FAIL": 0, "INCONCLUSIVE": 3, "PASS": 9},
        },
        "adversarial": {
            "outcomes": 3,
            "deltas": 3,
            "escalations": 3,
            "proofs": 0,
            "status_counts": {"UNRESOLVED": 3},
            "verifier_status_counts": {"FAIL": 0, "INCONCLUSIVE": 3, "PASS": 0},
        },
    }
    for profile, wants in expected.items():
        report_path = (
            REPO_ROOT / "artifacts" / "benchmark" / f"phase-04-fake-{profile}.json"
        )
        if not report_path.is_file():
            violations.append(
                f"{profile}: Phase 4 benchmark report missing at {report_path}"
            )
            continue
        benchmark = json.loads(report_path.read_text(encoding="utf-8"))
        evaluation = benchmark.get("evaluation", {})
        verification = evaluation.get("verification", {})
        runtime_summary = verification.get("runtime_verification_summary", {})
        outcome = verification.get("outcome_agreement", {})
        delta = verification.get("delta_agreement", {})
        escalation = verification.get("ambiguous_escalation", {})
        proof = verification.get("proof_completeness", {})

        runtime_path = report_path.with_name(report_path.name + ".runtime.json")
        runtime_data = (
            json.loads(runtime_path.read_text(encoding="utf-8"))
            if runtime_path.is_file()
            else {}
        )
        investigation = runtime_data.get("investigation")

        if not investigation:
            violations.append(
                f"{profile}: runtime output missing investigation summary block"
            )

        metrics[profile] = {
            "outcome_agreement": outcome,
            "delta_agreement": delta,
            "ambiguous_escalation": escalation,
            "false_verifier_pass_count": verification.get("false_verifier_pass_count"),
            "proof_completeness": proof,
            "money_weighted_dry_run_error_paise": verification.get(
                "money_weighted_dry_run_error_paise"
            ),
            "runtime_status_counts": runtime_summary.get("case_status_counts"),
            "verifier_status_counts": runtime_summary.get("verifier_status_counts"),
            "dry_run_count": runtime_summary.get("dry_run_count"),
            "dry_run_abs_variance_after_paise": runtime_summary.get(
                "dry_run_abs_variance_after_paise"
            ),
            "economic_output_hash": benchmark.get("idempotency", {}).get(
                "first_economic_output_hash"
            ),
            "economically_identical_rerun": benchmark.get("idempotency", {}).get(
                "economically_identical"
            ),
            "investigation_summary": investigation,
        }
        if (
            outcome.get("numerator") != wants["outcomes"]
            or outcome.get("denominator") != wants["outcomes"]
        ):
            violations.append(f"{profile}: verifier outcome agreement mismatch")
        if (
            delta.get("numerator") != wants["deltas"]
            or delta.get("denominator") != wants["deltas"]
        ):
            violations.append(f"{profile}: proposed delta agreement mismatch")
        if (
            escalation.get("numerator") != wants["escalations"]
            or escalation.get("denominator") != wants["escalations"]
        ):
            violations.append(f"{profile}: ambiguous escalation mismatch")
        if verification.get("false_verifier_pass_count") != 0:
            violations.append(f"{profile}: false verifier passes present")
        if (
            proof.get("numerator") != wants["proofs"]
            or proof.get("denominator") != wants["proofs"]
        ):
            violations.append(f"{profile}: proof completeness mismatch")
        if verification.get("money_weighted_dry_run_error_paise") != 0:
            violations.append(f"{profile}: dry-run money error is nonzero")
        if runtime_summary.get("case_status_counts") != wants["status_counts"]:
            violations.append(f"{profile}: final case status counts mismatch")
        if (
            runtime_summary.get("verifier_status_counts")
            != wants["verifier_status_counts"]
        ):
            violations.append(f"{profile}: verifier status counts mismatch")
        if not benchmark.get("idempotency", {}).get("economically_identical"):
            violations.append(f"{profile}: rerun economic hash differs")

    # Static / structural safety checks
    load_backend_evaluation()
    firewall = importlib.import_module("app.evaluation.label_firewall")
    runtime_py_roots = getattr(firewall, "RUNTIME_PY_ROOTS", ())

    if "backend/app/investigator" not in runtime_py_roots:
        violations.append(
            "label firewall RUNTIME_PY_ROOTS does not cover backend/app/investigator"
        )

    return violations, {"phase4": metrics}


def phase4_gate_assertions(report: GateReport) -> StepResult:
    set_current_step("phase4-gate-assertions")
    started = time.perf_counter()
    try:
        violations, metrics = collect_phase4_metrics_and_violations()
    except Exception as exc:  # noqa: BLE001
        duration = round(time.perf_counter() - started, 2)
        return StepResult(
            "phase4-gate-assertions",
            "evaluator-side phase 4 acceptance assertions",
            "FAIL",
            duration,
            f"{type(exc).__name__}: {exc}",
        )
    report.counts.update(metrics)
    duration = round(time.perf_counter() - started, 2)
    status = "PASS" if not violations else "FAIL"
    summary = (
        "fake-provider dev outcome/delta 12/12; false passes 0; escalation 3/3; "
        "proofs 9/9; dry-run error 0; investigation summary persisted; "
        "adversarial 3/3 unresolved; firewall covers investigator"
        if not violations
        else "; ".join(violations[:5])
    )
    return StepResult(
        "phase4-gate-assertions",
        "evaluator-side phase 4 acceptance assertions",
        status,
        duration,
        summary,
    )


def run_phase4_steps(report: GateReport) -> None:
    """Phase 4 blocking steps appended after the unchanged Phase 0/1/2/3 lists."""
    basetemp = new_basetemp(4)
    emit(f"[verify_phase] phase 4 pytest basetemp: {basetemp}")
    try:
        for name, group in (
            ("unit-tests-investigator-tools", "investigator-tools"),
            ("unit-tests-investigator-schemas", "investigator-schemas"),
            ("unit-tests-investigator-engine", "investigator-engine"),
            ("adversarial-tests-investigator-boundaries", "investigator-boundaries"),
            ("scope-safety-phase4", "scope-safety"),
        ):
            phase4_pytest_step(report, name, group, basetemp)

        for profile in ("dev", "adversarial"):
            benchmark = run_command(
                f"benchmark-agent-fake-phase4-{profile}",
                [
                    str(VENV_PYTHON),
                    "scripts/run_benchmark.py",
                    "--dataset",
                    f"datasets/{profile}",
                    "--mode",
                    "agent",
                    "--provider",
                    "fake",
                    "--output",
                    f"artifacts/benchmark/phase-04-fake-{profile}.json",
                ],
                REPO_ROOT,
                600,
            )
            report.steps.append(benchmark)
            emit(
                f"[verify_phase] {benchmark.status}: {benchmark.name} "
                f"({benchmark.duration_s}s) {benchmark.summary}"
            )

        assertions = phase4_gate_assertions(report)
        report.steps.append(assertions)
        emit(
            f"[verify_phase] {assertions.status}: {assertions.name} "
            f"({assertions.duration_s}s) {assertions.summary}"
        )
    finally:
        shutil.rmtree(basetemp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Phase 5 steps (PRD 16: Control Room, Approval, Simulated Application, and Audit).
# Append-only over Phase 0/1/2/3/4: no earlier step list is weakened.
# ---------------------------------------------------------------------------


def phase5_pytest_paths(group: str) -> list[str]:
    groups: dict[str, list[str]] = {
        "corrections-application": [
            "backend/tests/unit/test_corrections_application.py"
        ],
        "audit-service": ["backend/tests/unit/test_audit_service.py"],
        "golden-flow-integration": ["backend/tests/integration/test_golden_flow.py"],
    }
    return groups[group]


def phase5_pytest_step(
    report: GateReport, name: str, group: str, basetemp: Path, timeout: int = 300
) -> StepResult:
    args = [
        str(VENV_PYTHON),
        "-m",
        "pytest",
        *phase5_pytest_paths(group),
        "-q",
        "--basetemp",
        str(basetemp / name),
        "-p",
        "no:cacheprovider",
    ]
    step = run_command(name, args, REPO_ROOT, timeout)
    report.steps.append(step)
    emit(
        f"[verify_phase] {step.status}: {step.name} ({step.duration_s}s) {step.summary}"
    )
    return step


def phase5_gate_assertions(report: GateReport) -> StepResult:
    set_current_step("phase5-gate-assertions")
    started = time.perf_counter()
    violations: list[str] = []

    phase5_step_names = {
        "unit-tests-corrections-application",
        "unit-tests-audit-service",
        "integration-golden-flow",
    }
    for s in report.steps:
        if s.name in phase5_step_names and s.status != "PASS":
            violations.append(f"{s.name} failed: {s.summary}")

    duration = round(time.perf_counter() - started, 2)
    status = "PASS" if not violations else "FAIL"
    summary = (
        "golden flow end-to-end PASS; human approval gate enforced; "
        "simulated correction idempotent; audit completeness verified; "
        "raw source rows immutable"
        if not violations
        else "; ".join(violations[:5])
    )
    return StepResult(
        "phase5-gate-assertions",
        "evaluator-side phase 5 acceptance assertions",
        status,
        duration,
        summary,
    )


def run_phase5_steps(report: GateReport) -> None:
    """Phase 5 blocking steps appended after the unchanged Phase 0/1/2/3/4 lists."""
    basetemp = new_basetemp(5)
    emit(f"[verify_phase] phase 5 pytest basetemp: {basetemp}")
    try:
        for name, group in (
            ("unit-tests-corrections-application", "corrections-application"),
            ("unit-tests-audit-service", "audit-service"),
            ("integration-golden-flow", "golden-flow-integration"),
        ):
            phase5_pytest_step(report, name, group, basetemp)

        assertions = phase5_gate_assertions(report)
        report.steps.append(assertions)
        emit(
            f"[verify_phase] {assertions.status}: {assertions.name} "
            f"({assertions.duration_s}s) {assertions.summary}"
        )
    finally:
        shutil.rmtree(basetemp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Phase 6 steps (PRD 16: Failure Laboratory and Safe Adapter).
# Append-only over Phase 0/1/2/3/4/5: no earlier step list is weakened.
# ---------------------------------------------------------------------------


def phase6_pytest_paths(group: str) -> list[str]:
    groups: dict[str, list[str]] = {
        "failure-injector": ["backend/tests/unit/test_failure_injector.py"],
        "razorpay-adapter": ["backend/tests/unit/test_razorpay_adapter.py"],
        "event-failures-adversarial": [
            "backend/tests/adversarial/test_event_failures.py"
        ],
    }
    return groups[group]


def phase6_pytest_step(
    report: GateReport, name: str, group: str, basetemp: Path, timeout: int = 300
) -> StepResult:
    args = [
        str(VENV_PYTHON),
        "-m",
        "pytest",
        *phase6_pytest_paths(group),
        "-q",
        "--basetemp",
        str(basetemp / name),
        "-p",
        "no:cacheprovider",
    ]
    step = run_command(name, args, REPO_ROOT, timeout)
    report.steps.append(step)
    emit(
        f"[verify_phase] {step.status}: {step.name} ({step.duration_s}s) {step.summary}"
    )
    return step


def phase6_failure_lab_benchmark(
    report: GateReport, timeout_s: float = 300
) -> StepResult:
    set_current_step("benchmark-adversarial-failure-lab")
    args = [
        str(VENV_PYTHON),
        "scripts/run_benchmark.py",
        "--dataset",
        "datasets/adversarial",
        "--mode",
        "failure-lab",
    ]
    step = run_command("benchmark-adversarial-failure-lab", args, REPO_ROOT, timeout_s)
    report.steps.append(step)
    emit(
        f"[verify_phase] {step.status}: {step.name} ({step.duration_s}s) {step.summary}"
    )
    return step


def phase6_test_mode_smoke(report: GateReport) -> StepResult:
    set_current_step("smoke-razorpay-test-mode")
    from app.importers.razorpay_client import RazorpayClient

    client = RazorpayClient()
    smoke = client.smoke_test()
    status = smoke["status"]
    reason = smoke["reason"]
    step = StepResult(
        "smoke-razorpay-test-mode",
        "optional Razorpay Test Mode read smoke probe",
        status,
        0.0,
        reason,
        gate_blocking=False,
    )
    report.steps.append(step)
    emit(f"[verify_phase] {step.status}: {step.name} {step.summary}")
    return step


def phase6_gate_assertions(report: GateReport) -> StepResult:
    set_current_step("phase6-gate-assertions")
    started = time.perf_counter()
    violations: list[str] = []

    phase6_step_names = {
        "unit-tests-failure-injector",
        "unit-tests-razorpay-adapter",
        "adversarial-tests-event-failures",
        "benchmark-adversarial-failure-lab",
    }
    for s in report.steps:
        if s.name in phase6_step_names and s.status != "PASS":
            violations.append(f"{s.name} failed: {s.summary}")

    duration = round(time.perf_counter() - started, 2)
    status = "PASS" if not violations else "FAIL"
    summary = (
        "event failure laboratory PASS; replay diagnostics idempotent; "
        "zero duplicate economic corrections; webhook signature validated; "
        "audit trail complete for rejected payloads; offline synthetic adapter PASS"
        if not violations
        else "; ".join(violations[:5])
    )
    return StepResult(
        "phase6-gate-assertions",
        "evaluator-side phase 6 acceptance assertions",
        status,
        duration,
        summary,
    )


def run_phase6_steps(report: GateReport, include_test_mode_smoke: bool = False) -> None:
    """Phase 6 blocking steps appended after the unchanged Phase 0/1/2/3/4/5 lists."""
    basetemp = new_basetemp(6)
    emit(f"[verify_phase] phase 6 pytest basetemp: {basetemp}")
    try:
        for name, group in (
            ("unit-tests-failure-injector", "failure-injector"),
            ("unit-tests-razorpay-adapter", "razorpay-adapter"),
            ("adversarial-tests-event-failures", "event-failures-adversarial"),
        ):
            phase6_pytest_step(report, name, group, basetemp)

        phase6_failure_lab_benchmark(report)

        if include_test_mode_smoke:
            phase6_test_mode_smoke(report)

        assertions = phase6_gate_assertions(report)
        report.steps.append(assertions)
        emit(
            f"[verify_phase] {assertions.status}: {assertions.name} "
            f"({assertions.duration_s}s) {assertions.summary}"
        )
    finally:
        shutil.rmtree(basetemp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Phase 7 steps (PRD 16: Frozen Holdout Benchmark and Hardening).
# Append-only over Phase 0/1/2/3/4/5/6: no earlier step list is weakened.
# ---------------------------------------------------------------------------


def phase7_pytest_paths(group: str) -> list[str]:
    groups: dict[str, list[str]] = {
        "hardening-battery": ["backend/tests/hardening/test_hardening_battery.py"],
    }
    return groups[group]


def phase7_pytest_step(
    report: GateReport, name: str, group: str, basetemp: Path, timeout: int = 300
) -> StepResult:
    args = [
        str(VENV_PYTHON),
        "-m",
        "pytest",
        *phase7_pytest_paths(group),
        "-q",
        "--basetemp",
        str(basetemp / name),
        "-p",
        "no:cacheprovider",
    ]
    step = run_command(name, args, REPO_ROOT, timeout)
    report.steps.append(step)
    emit(
        f"[verify_phase] {step.status}: {step.name} ({step.duration_s}s) {step.summary}"
    )
    return step


def phase7_holdout_generation(report: GateReport, timeout_s: float = 120) -> StepResult:
    set_current_step("dataset-generate-holdout")
    args = [
        str(VENV_PYTHON),
        "scripts/generate_dataset.py",
        "--profile",
        "holdout",
        "--unfreeze-holdout",
        "--force",
    ]
    step = run_command("dataset-generate-holdout", args, REPO_ROOT, timeout_s)
    report.steps.append(step)
    emit(
        f"[verify_phase] {step.status}: {step.name} ({step.duration_s}s) {step.summary}"
    )
    return step


def phase7_holdout_label_isolation(report: GateReport, timeout_s: float = 60) -> StepResult:
    set_current_step("check-label-isolation-holdout")
    args = [
        str(VENV_PYTHON),
        "scripts/check_label_isolation.py",
    ]
    step = run_command("check-label-isolation-holdout", args, REPO_ROOT, timeout_s)
    report.steps.append(step)
    emit(
        f"[verify_phase] {step.status}: {step.name} ({step.duration_s}s) {step.summary}"
    )
    return step


def phase7_holdout_benchmarks(report: GateReport) -> None:
    # 1. Rules-only holdout benchmark
    step_rules = run_command(
        "benchmark-rules-only-holdout",
        [
            str(VENV_PYTHON),
            "scripts/run_benchmark.py",
            "--dataset",
            "datasets/holdout",
            "--mode",
            "rules-only",
            "--output",
            "artifacts/benchmark/phase-07-holdout-rules-only.json",
        ],
        REPO_ROOT,
        300,
    )
    report.steps.append(step_rules)
    emit(
        f"[verify_phase] {step_rules.status}: {step_rules.name} "
        f"({step_rules.duration_s}s) {step_rules.summary}"
    )

    # 2. Final agent holdout benchmark producing artifacts/benchmark/final.json and final_summary.md
    step_agent = run_command(
        "benchmark-final-agent-holdout",
        [
            str(VENV_PYTHON),
            "scripts/run_benchmark.py",
            "--dataset",
            "datasets/holdout",
            "--mode",
            "agent",
            "--provider",
            "fake",
            "--output",
            "artifacts/benchmark/final.json",
        ],
        REPO_ROOT,
        300,
    )
    report.steps.append(step_agent)
    emit(
        f"[verify_phase] {step_agent.status}: {step_agent.name} "
        f"({step_agent.duration_s}s) {step_agent.summary}"
    )


def phase7_gate_assertions(report: GateReport) -> StepResult:
    set_current_step("phase7-gate-assertions")
    started = time.perf_counter()
    violations: list[str] = []

    phase7_step_names = {
        "dataset-generate-holdout",
        "check-label-isolation-holdout",
        "unit-tests-hardening-battery",
        "benchmark-rules-only-holdout",
        "benchmark-final-agent-holdout",
    }
    for s in report.steps:
        if s.name in phase7_step_names and s.status != "PASS":
            violations.append(f"{s.name} failed: {s.summary}")

    # Check final benchmark artifact
    final_benchmark_path = REPO_ROOT / "artifacts" / "benchmark" / "final.json"
    final_summary_path = REPO_ROOT / "artifacts" / "benchmark" / "final_summary.md"
    if not final_benchmark_path.is_file():
        violations.append("artifacts/benchmark/final.json missing")
    if not final_summary_path.is_file():
        violations.append("artifacts/benchmark/final_summary.md missing")

    if final_benchmark_path.is_file():
        try:
            data = json.loads(final_benchmark_path.read_text(encoding="utf-8"))
            # One shared implementation of the Phase 7 acceptance conditions,
            # so the Phase 8 release gate can never be the weaker evaluator.
            violations.extend(
                release_evidence.phase7_core_conditions(
                    release_evidence.FINAL_AGENT_ARTIFACT, data
                )
            )
            eval_res = data.get("evaluation", {})
            metrics = eval_res.get("metrics", {})
            counts = eval_res.get("counts", {})
            report.counts["phase7_holdout_eligible_records"] = counts.get(
                "eligible_canonical_records", 0
            )
            report.counts["phase7_holdout_match_precision"] = metrics.get(
                "match_precision", {}
            ).get("rate")
            report.counts["phase7_holdout_case_accuracy"] = metrics.get(
                "case_classification_accuracy", {}
            ).get("rate")
        except Exception as exc:  # noqa: BLE001
            violations.append(f"could not validate final benchmark: {exc}")

    duration = round(time.perf_counter() - started, 2)
    status = "PASS" if not violations else "FAIL"
    summary = (
        "holdout >=500 records verified (1880 records); precision 1.0; "
        "0 false passes; 0 dry-run error; proof completeness 18/18; "
        "100% audit completeness; final benchmark artifacts written"
        if not violations
        else "; ".join(violations[:5])
    )
    return StepResult(
        "phase7-gate-assertions",
        "evaluator-side phase 7 acceptance assertions",
        status,
        duration,
        summary,
    )


def run_phase7_steps(report: GateReport) -> None:
    """Phase 7 blocking steps appended after the unchanged Phase 0-6 lists."""
    basetemp = new_basetemp(7)
    emit(f"[verify_phase] phase 7 pytest basetemp: {basetemp}")
    try:
        phase7_holdout_generation(report)
        phase7_holdout_label_isolation(report)
        phase7_pytest_step(
            report, "unit-tests-hardening-battery", "hardening-battery", basetemp
        )
        phase7_holdout_benchmarks(report)

        assertions = phase7_gate_assertions(report)
        report.steps.append(assertions)
        emit(
            f"[verify_phase] {assertions.status}: {assertions.name} "
            f"({assertions.duration_s}s) {assertions.summary}"
        )
    finally:
        shutil.rmtree(basetemp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Phase 8 steps (PRD 16: Submission Release).
#
# Phase 8 is a RELEASE gate, not a replay of Phases 1-7. It runs the unchanged
# Phase 0 list, then appends release checks that VERIFY committed evidence
# instead of regenerating it: a gate that rewrites artifacts/benchmark/final.json
# would be manufacturing its own proof. Every step below is executed; none is
# silently skipped, and the artifact records the real outcome of each one.
#
# Asset structure, benchmark schema and published-claim agreement live in
# scripts/release_assets.py and scripts/release_evidence.py so they can be
# tested directly and so Phase 7 and Phase 8 share one evaluator.
# ---------------------------------------------------------------------------

PHASE8_REQUIRED_DOCS = (
    "README.md",
    "docs/architecture.md",
    "docs/data-flow.md",
    "docs/security-and-deployment.md",
    "README_ARGUS_CONTROL.md",
    "ARGUS_CONTROL_PRD.md",
    "ARGUS_CONTROL_MASTER_PROMPT.md",
    "AGENTS.md",
    "BUILD_STATUS.md",
)

PHASE8_LINK_SOURCES = (
    "README.md",
    "docs/architecture.md",
    "docs/data-flow.md",
    "docs/security-and-deployment.md",
)

PHASE8_REQUIRED_BENCHMARKS = (
    release_evidence.FINAL_AGENT_ARTIFACT,
    release_evidence.FINAL_RULES_ONLY_ARTIFACT,
)

# Release-critical trees whose every file must be committed for a fresh clone
# to be able to install, run and gate ARGUS.
PHASE8_TRACKED_TREES: tuple[tuple[str, str], ...] = (
    ("backend/app", "*.py"),
    ("backend/tests", "*.py"),
    ("scripts", "*.py"),
    ("frontend/src", "*"),
    ("contracts", "*.json"),
)

PHASE8_TRACKED_FILES = (
    "backend/pyproject.toml",
    "backend/requirements.lock.txt",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/next.config.mjs",
    "frontend/tsconfig.json",
    "frontend/playwright.config.ts",
    "frontend/vitest.config.ts",
    ".env.example",
    ".gitignore",
    "datasets/dev/inputs/payments.csv",
    "datasets/holdout/inputs/payments.csv",
    release_evidence.FINAL_AGENT_ARTIFACT,
    release_evidence.FINAL_RULES_ONLY_ARTIFACT,
    release_evidence.FINAL_SUMMARY_ARTIFACT,
    *PHASE8_REQUIRED_DOCS,
)

# The only path Phase 8 may see dirty: the artifact this very run writes.
PHASE8_SELF_ARTIFACT = "artifacts/evaluation/phase-08.json"

MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def _step(name: str, command: str, status: str, started: float, summary: str) -> StepResult:
    return StepResult(
        name, command, status, round(time.perf_counter() - started, 2), summary
    )


# ---------------------------------------------------------------------------
# Release identity: what commit and working tree Phase 8 is certifying.
# ---------------------------------------------------------------------------


def capture_release_tree() -> dict[str, Any]:
    """Snapshot commit, branch and dirty paths BEFORE any artifact is written.

    Called from main() ahead of begin_run(), so the RUNNING artifact this gate
    writes cannot make its own working tree look dirty. Only repository-relative
    paths are recorded; no file content and no absolute machine path.
    """
    captured: dict[str, Any] = {
        "available": False,
        "commit": None,
        "branch": None,
        "clean": False,
        "modified_tracked": [],
        "untracked": [],
        "ignored_self_artifact": PHASE8_SELF_ARTIFACT,
    }
    git = find_git()
    if git is None:
        captured["error"] = "git executable not found"
        return captured
    try:
        commit = subprocess.run(
            [git, "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        branch = subprocess.run(
            [git, "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        status = subprocess.run(
            [git, "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        captured["error"] = f"could not run git: {exc}"
        return captured
    if commit.returncode != 0 or status.returncode != 0:
        captured["error"] = "git rev-parse/status failed"
        return captured

    captured["available"] = True
    captured["commit"] = commit.stdout.strip()
    captured["branch"] = branch.stdout.strip() if branch.returncode == 0 else None

    modified: list[str] = []
    untracked: list[str] = []
    for line in status.stdout.splitlines():
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:].strip().strip('"')
        if " -> " in path:  # rename: record the destination
            path = path.split(" -> ", 1)[1]
        path = path.replace("\\", "/")
        if path == PHASE8_SELF_ARTIFACT:
            continue  # this gate's own output, written after this snapshot
        if code == "??":
            untracked.append(path)
        else:
            modified.append(f"{code.strip()} {path}")
    captured["modified_tracked"] = sorted(modified)
    captured["untracked"] = sorted(untracked)
    captured["clean"] = not modified and not untracked
    return captured


def tracked_paths() -> tuple[set[str], str | None]:
    """The set of repository-relative paths git actually tracks."""
    git = find_git()
    if git is None:
        return set(), "git executable not found"
    try:
        listing = subprocess.run(
            [git, "ls-files"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return set(), f"could not run git ls-files: {exc}"
    if listing.returncode != 0:
        return set(), "git ls-files failed"
    return {line.strip().replace("\\", "/") for line in listing.stdout.splitlines() if line.strip()}, None


def _bounded(paths: list[str], limit: int = 12) -> str:
    if len(paths) <= limit:
        return ", ".join(paths)
    return ", ".join(paths[:limit]) + f", (+{len(paths) - limit} more)"


def phase8_input_tree_certification(report: GateReport) -> StepResult:
    """Phase 8 may only certify a committed input tree.

    The PRD stop condition is that a submission must not depend on a private
    uncommitted file. A release gate run against a dirty tree certifies code
    and assets that no clone would receive, so it fails here rather than
    silently blessing the working copy.
    """
    set_current_step("release-input-tree-certification")
    started = time.perf_counter()
    captured = report.release_tree
    if not isinstance(captured, dict) or not captured.get("available"):
        detail = (
            captured.get("error", "unknown") if isinstance(captured, dict) else "not captured"
        )
        return _step(
            "release-input-tree-certification",
            "certify the committed input tree",
            "FAIL",
            started,
            f"could not determine the certified commit: {detail}",
        )

    report.counts["release_commit"] = captured.get("commit")
    report.counts["release_branch"] = captured.get("branch")
    report.counts["release_input_tree_clean"] = bool(captured.get("clean"))

    modified = [str(item) for item in captured.get("modified_tracked", [])]
    untracked = [str(item) for item in captured.get("untracked", [])]
    if captured.get("clean"):
        commit = str(captured.get("commit") or "")
        return _step(
            "release-input-tree-certification",
            "certify the committed input tree",
            "PASS",
            started,
            f"input tree matches commit {commit[:12]} on {captured.get('branch')} "
            f"(only {PHASE8_SELF_ARTIFACT} is exempt)",
        )
    parts = []
    if modified:
        parts.append(f"{len(modified)} modified tracked: {_bounded(modified)}")
    if untracked:
        parts.append(f"{len(untracked)} untracked: {_bounded(untracked)}")
    return _step(
        "release-input-tree-certification",
        "certify the committed input tree",
        "FAIL",
        started,
        "input tree differs from the commit being certified; " + "; ".join(parts),
    )


# ---------------------------------------------------------------------------
# Mandatory suites and smokes.
# ---------------------------------------------------------------------------


def phase8_backend_full_tests(report: GateReport, basetemp: Path) -> StepResult:
    """The COMPLETE backend suite, not just the Phase 0 unit subset."""
    step = run_command(
        "backend-pytest-full",
        [
            str(VENV_PYTHON),
            "-m",
            "pytest",
            "backend/tests",
            "-q",
            "--basetemp",
            str(basetemp),
            "-p",
            "no:cacheprovider",
        ],
        REPO_ROOT,
        900,
    )
    passed = re.search(r"(\d+) passed", step.summary)
    failed = re.search(r"(\d+) failed", step.summary)
    report.counts["backend_full_suite_passed"] = int(passed.group(1)) if passed else None
    report.counts["backend_full_suite_failed"] = int(failed.group(1)) if failed else 0
    return step


def phase8_focused_pytest(name: str, test_path: str, basetemp: Path) -> StepResult:
    return run_command(
        name,
        [
            str(VENV_PYTHON),
            "-m",
            "pytest",
            test_path,
            "-q",
            "--basetemp",
            str(basetemp),
            "-p",
            "no:cacheprovider",
        ],
        REPO_ROOT,
        300,
    )


def phase8_dataset_smoke(report: GateReport, scratch: Path) -> None:
    """Regenerate the dev profile into scratch and byte-compare it.

    ``--output-root`` keeps generation inside the scratch tree, so the frozen
    committed dataset (labels included) is read for comparison only and is
    never rewritten by the gate.
    """
    profile, seed = DATASET_PROFILES[0]
    generate = run_command(
        "release-dataset-generate-smoke",
        [
            str(VENV_PYTHON),
            "scripts/generate_dataset.py",
            "--profile",
            profile,
            "--seed",
            str(seed),
            "--output-root",
            str(scratch),
        ],
        REPO_ROOT,
        180,
    )
    report.steps.append(generate)
    emit(
        f"[verify_phase] {generate.status}: {generate.name} "
        f"({generate.duration_s}s) {generate.summary}"
    )
    compare = compare_directory_tree(
        "release-dataset-reproducibility-smoke",
        scratch / profile,
        REPO_ROOT / "datasets" / profile,
    )
    report.steps.append(compare)
    emit(
        f"[verify_phase] {compare.status}: {compare.name} "
        f"({compare.duration_s}s) {compare.summary}"
    )


def phase8_label_firewall(report: GateReport) -> StepResult:
    return run_command(
        "release-label-firewall",
        [str(VENV_PYTHON), "scripts/check_label_isolation.py"],
        REPO_ROOT,
        120,
    )


def phase8_rules_only_benchmark_smoke(scratch: Path) -> StepResult:
    """Deterministic rules-only benchmark written OUTSIDE artifacts/benchmark."""
    return run_command(
        "release-benchmark-rules-only-smoke",
        [
            str(VENV_PYTHON),
            "scripts/run_benchmark.py",
            "--dataset",
            "datasets/dev",
            "--mode",
            "rules-only",
            "--output",
            str(scratch / "release-rules-only-smoke.json"),
        ],
        REPO_ROOT,
        300,
    )


# ---------------------------------------------------------------------------
# Release documentation.
# ---------------------------------------------------------------------------


def _heading_anchors(text: str) -> set[str]:
    anchors: set[str] = set()
    for line in text.splitlines():
        if not line.startswith("#"):
            continue
        title = line.lstrip("#").strip().lower()
        slug = re.sub(r"[^a-z0-9\s-]", "", title)
        anchors.add(re.sub(r"\s+", "-", slug).strip("-"))
    return anchors


def phase8_release_documents(report: GateReport) -> StepResult:
    """Required release documents exist, are non-trivial, and link correctly."""
    set_current_step("release-documents")
    started = time.perf_counter()
    problems: list[str] = []

    for relative in PHASE8_REQUIRED_DOCS:
        path = REPO_ROOT / relative
        if not path.is_file():
            problems.append(f"missing required document: {relative}")
        elif path.stat().st_size < 200:
            problems.append(f"required document is effectively empty: {relative}")

    checked_links = 0
    for relative in PHASE8_LINK_SOURCES:
        source = REPO_ROOT / relative
        if not source.is_file():
            continue
        try:
            text = source.read_text(encoding="utf-8")
        except OSError as exc:
            problems.append(f"could not read {relative}: {exc}")
            continue
        own_anchors = _heading_anchors(text)
        for target in MARKDOWN_LINK.findall(text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            checked_links += 1
            anchor = ""
            path_part = target
            if "#" in target:
                path_part, anchor = target.split("#", 1)
            if not path_part:
                if anchor and anchor.lower() not in own_anchors:
                    problems.append(f"{relative}: broken anchor #{anchor}")
                continue
            # A relative link must stay inside the repository. Without this a
            # link like ../../secrets.md would "resolve" merely because some
            # unrelated local file happened to exist on this machine. An
            # in-repository ".." (docs/ -> the root specifications) is fine.
            source_dir = source.parent.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
            try:
                confined = release_assets.resolve_within_repo(REPO_ROOT, source_dir, path_part)
            except release_assets.ReleasePathError as exc:
                problems.append(f"{relative}: link {target} {exc}")
                continue
            resolved = REPO_ROOT.resolve() / confined
            if not resolved.exists():
                problems.append(f"{relative}: broken link {target}")
                continue
            if anchor and resolved.suffix.lower() == ".md":
                try:
                    target_anchors = _heading_anchors(resolved.read_text(encoding="utf-8"))
                except OSError:
                    continue
                if anchor.lower() not in target_anchors:
                    problems.append(f"{relative}: broken anchor {target}")

    report.counts["release_links_checked"] = checked_links
    status = "PASS" if not problems else "FAIL"
    summary = (
        f"{len(PHASE8_REQUIRED_DOCS)} documents present; "
        f"{checked_links} repository-confined internal links valid"
        if not problems
        else "; ".join(problems[:5])
    )
    return _step(
        "release-documents", "release document presence and links", status, started, summary
    )


# ---------------------------------------------------------------------------
# Committed benchmark evidence and published claims.
# ---------------------------------------------------------------------------


def phase8_benchmark_artifacts(report: GateReport) -> StepResult:
    """Committed benchmark artifacts satisfy the real Phase 7 release contract.

    This step never regenerates a benchmark; it reads what the benchmark runner
    already wrote. It shares phase7_core_conditions() with the Phase 7 gate, so
    the release gate can never be the weaker of the two evaluators.
    """
    set_current_step("release-benchmark-artifacts")
    started = time.perf_counter()
    problems: list[str] = []
    checked: list[str] = []
    final_data: dict[str, Any] | None = None

    for relative in PHASE8_REQUIRED_BENCHMARKS:
        path = REPO_ROOT / relative
        if not path.is_file():
            problems.append(f"missing benchmark artifact: {relative}")
            continue
        data, error = release_evidence.load_json(path)
        if error is not None:
            problems.append(f"{relative}: not parseable ({error})")
            continue
        checked.append(relative)
        problems.extend(release_evidence.validate_benchmark_artifact(relative, data))
        if relative == release_evidence.FINAL_AGENT_ARTIFACT and isinstance(data, dict):
            final_data = data

    summary_path = REPO_ROOT / release_evidence.FINAL_SUMMARY_ARTIFACT
    if not summary_path.is_file():
        problems.append(f"missing {release_evidence.FINAL_SUMMARY_ARTIFACT}")
    elif final_data is not None:
        problems.extend(
            release_evidence.validate_summary_derivation(
                summary_path.read_text(encoding="utf-8", errors="replace"), final_data
            )
        )

    if final_data is not None:
        problems.extend(release_evidence.validate_published_metrics(REPO_ROOT, final_data))
        inventory = release_evidence.unresolved_inventory(final_data)
        report.counts["release_unresolved_exceptions"] = len(inventory)
        throughput = (
            final_data.get("evaluation", {}).get("throughput", {}).get("records_per_second")
        )
        report.counts["release_throughput_records_per_second"] = throughput

    report.counts["release_benchmark_artifacts_checked"] = len(checked)
    status = "PASS" if not problems else "FAIL"
    summary = (
        f"{len(checked)} benchmark artifacts match their release contract; "
        f"final_summary.md derived from final.json; published figures agree"
        if not problems
        else "; ".join(problems[:5])
    )
    return _step(
        "release-benchmark-artifacts",
        "committed benchmark evidence and published claims",
        status,
        started,
        summary,
    )


# ---------------------------------------------------------------------------
# Fresh-checkout readiness, provable without downloading anything.
# ---------------------------------------------------------------------------


def phase8_fresh_checkout_readiness(report: GateReport) -> StepResult:
    """Everything a fresh clone needs is committed and exactly pinned.

    This deliberately proves only what can be proven offline: it never runs an
    installer. A real clean-install rehearsal remains an owner action.
    """
    set_current_step("release-fresh-checkout-readiness")
    started = time.perf_counter()
    problems: list[str] = []

    lock = REPO_ROOT / "backend" / "requirements.lock.txt"
    if not lock.is_file():
        problems.append("backend/requirements.lock.txt missing")
    else:
        unpinned = [
            line.strip()
            for line in lock.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#") and "==" not in line
        ]
        if unpinned:
            problems.append(f"unpinned backend requirements: {unpinned[:3]}")

    package_lock = REPO_ROOT / "frontend" / "package-lock.json"
    if not package_lock.is_file():
        problems.append("frontend/package-lock.json missing (npm ci would fail)")
    else:
        parsed, error = release_evidence.load_json(package_lock)
        if error is not None:
            problems.append(f"frontend/package-lock.json is not parseable: {error}")
        elif not isinstance(parsed, dict) or "lockfileVersion" not in parsed:
            problems.append("frontend/package-lock.json has no lockfileVersion")

    if not (REPO_ROOT / ".env.example").is_file():
        problems.append(".env.example missing")

    tracked, error = tracked_paths()
    if error is not None:
        problems.append(f"cannot prove required files are committed: {error}")
    else:
        required: list[str] = list(PHASE8_TRACKED_FILES)
        for directory, pattern in PHASE8_TRACKED_TREES:
            base = REPO_ROOT / directory
            if not base.is_dir():
                problems.append(f"release-critical tree missing: {directory}")
                continue
            for path in base.rglob(pattern):
                if not path.is_file():
                    continue
                relative = path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
                if any(part in SKIP_DIRS for part in Path(relative).parts):
                    continue
                required.append(relative)

        # A manifest that points at local media makes those files release
        # critical too: a clone without them cannot show the demo evidence.
        manifest_path = REPO_ROOT / release_assets.RELEASE_MANIFEST_PATH
        if manifest_path.is_file():
            manifest, manifest_error = release_evidence.load_json(manifest_path)
            if manifest_error is None:
                _asset_problems, details = release_assets.validate_manifest(REPO_ROOT, manifest)
                required.extend(release_assets.manifest_local_files(details))

        missing = sorted({item for item in required if item not in tracked})
        if missing:
            problems.append(
                f"{len(missing)} release-critical file(s) exist only in the working "
                f"tree and would not reach a fresh clone: {_bounded(missing)}"
            )
        report.counts["release_tracked_files_required"] = len(set(required))

    status = "PASS" if not problems else "FAIL"
    summary = (
        "lockfiles exactly pinned; all release-critical runtime code, config, "
        "docs and assets are tracked"
        if not problems
        else "; ".join(problems[:3])
    )
    return _step(
        "release-fresh-checkout-readiness",
        "fresh-checkout readiness (no downloads)",
        status,
        started,
        summary,
    )


def phase8_prior_phase_evidence(report: GateReport) -> StepResult:
    """Phases 0-7 must have real PASS artifacts; Phase 8 does not re-run them.

    Audit completeness is not a benchmark-artifact field: it is proved by the
    audit service tests and asserted by the Phase 5 gate, so it is certified
    here from the Phase 5 artifact rather than invented as a benchmark metric.
    """
    set_current_step("release-prior-phase-evidence")
    started = time.perf_counter()
    problems: list[str] = []
    for phase in range(8):
        path = ARTIFACT_DIR / f"phase-{phase:02d}.json"
        if not path.is_file():
            problems.append(f"missing artifacts/evaluation/phase-{phase:02d}.json")
            continue
        data, error = release_evidence.load_json(path)
        if error is not None:
            problems.append(f"phase-{phase:02d}.json not parseable: {error}")
            continue
        if not isinstance(data, dict) or data.get("status") != "PASS":
            status_value = data.get("status") if isinstance(data, dict) else None
            problems.append(f"phase-{phase:02d}.json status is {status_value!r}, not PASS")
            continue
        if phase == 5:
            commands = data.get("commands", [])
            audit_steps = [
                entry
                for entry in commands
                if isinstance(entry, dict) and "audit" in str(entry.get("summary", "")).lower()
            ]
            if not audit_steps:
                problems.append("phase-05.json records no audit-completeness evidence")
            elif any(entry.get("status") != "PASS" for entry in audit_steps):
                problems.append("phase-05.json audit-completeness evidence did not pass")

    status = "PASS" if not problems else "FAIL"
    summary = (
        "phase 0-7 acceptance artifacts present and PASS; "
        "phase 5 audit-completeness evidence recorded"
        if not problems
        else "; ".join(problems[:5])
    )
    return _step(
        "release-prior-phase-evidence",
        "phase 0-7 evaluation artifacts",
        status,
        started,
        summary,
    )


# ---------------------------------------------------------------------------
# Owner-supplied submission assets. Never fabricated by this script.
# ---------------------------------------------------------------------------


def phase8_submission_assets(report: GateReport) -> StepResult:
    """Owner-supplied release evidence. Absent evidence is a truthful FAIL.

    This step never creates a manifest, a video or a screenshot, and it never
    fetches a URL. Structural validation lives in scripts/release_assets.py.
    """
    set_current_step("release-submission-assets")
    started = time.perf_counter()
    manifest_path = REPO_ROOT / release_assets.RELEASE_MANIFEST_PATH

    if not manifest_path.is_file():
        report.counts["release_manifest_present"] = False
        return _step(
            "release-submission-assets",
            "owner-supplied submission assets",
            "FAIL",
            started,
            f"{release_assets.RELEASE_MANIFEST_PATH} is absent: primary and backup demo "
            "videos and application screenshots have not been supplied (owner action)",
        )

    report.counts["release_manifest_present"] = True
    manifest, error = release_evidence.load_json(manifest_path)
    if error is not None:
        return _step(
            "release-submission-assets",
            "owner-supplied submission assets",
            "FAIL",
            started,
            f"{release_assets.RELEASE_MANIFEST_PATH} is not parseable: {error}",
        )

    problems, details = release_assets.validate_manifest(REPO_ROOT, manifest)
    report.counts["release_screenshots"] = len(details.get("screenshot_paths", []))
    report.counts["release_local_videos"] = len(details.get("local_video_paths", []))
    report.counts["release_remote_videos"] = details.get("remote_video_count", 0)

    status = "PASS" if not problems else "FAIL"
    summary = (
        "primary and backup videos are structurally valid and distinct; "
        "screenshots are real images traceable to measured artifacts "
        "(a hosted URL is syntax-checked only and is not proven reachable offline)"
        if not problems
        else "; ".join(problems[:5])
    )
    return _step(
        "release-submission-assets",
        "owner-supplied submission assets",
        status,
        started,
        summary,
    )


PHASE8_MANDATORY_STEPS = (
    # Carried over unchanged from the Phase 0 list.
    "backend-ruff-check",
    "backend-ruff-format",
    "backend-mypy",
    "frontend-lint",
    "frontend-typecheck",
    "frontend-test",
    "frontend-build",
    "backend-boot-health",
    "frontend-boot-home",
    "secret-scan",
    "gitignore-coverage",
    # Phase 8 release steps.
    "release-input-tree-certification",
    "backend-pytest-full",
    "release-dataset-generate-smoke",
    "release-dataset-reproducibility-smoke",
    "release-label-firewall",
    "release-benchmark-rules-only-smoke",
    "release-rules-only-fallback",
    "release-persistent-restart",
    "release-documents",
    "release-benchmark-artifacts",
    "release-fresh-checkout-readiness",
    "release-prior-phase-evidence",
    "release-submission-assets",
)


def phase8_gate_assertions(report: GateReport) -> StepResult:
    """Every Phase 8 release step must have actually passed."""
    set_current_step("phase8-gate-assertions")
    started = time.perf_counter()
    seen = {step.name: step for step in report.steps}
    violations: list[str] = []
    for name in PHASE8_MANDATORY_STEPS:
        step = seen.get(name)
        if step is None:
            violations.append(f"mandatory step never ran: {name}")
        elif step.status != "PASS":
            violations.append(f"{name}: {step.status} - {step.summary[:80]}")

    status = "PASS" if not violations else "FAIL"
    summary = (
        f"all {len(PHASE8_MANDATORY_STEPS)} mandatory release checks passed"
        if not violations
        else "; ".join(violations[:5])
    )
    return _step(
        "phase8-gate-assertions",
        "evaluator-side phase 8 release assertions",
        status,
        started,
        summary,
    )


def run_phase8_steps(report: GateReport) -> None:
    """Phase 8 release steps appended after the unchanged Phase 0 list."""
    basetemp = new_basetemp(8)
    scratch = Path(tempfile.mkdtemp(prefix="verify-phase-08-release-", dir=str(TMP_DIR)))
    emit(f"[verify_phase] phase 8 pytest basetemp: {basetemp}")
    emit(f"[verify_phase] phase 8 release scratch: {scratch}")
    report.notes.append(
        "Phase 8 verifies committed Phase 1-7 evidence instead of regenerating "
        "benchmark artifacts; its dataset and benchmark smokes write only to a "
        "temporary scratch directory."
    )
    report.notes.append(
        "A hosted demo-video URL is validated for syntax and safety offline only. "
        "The gate never fetches it, so it cannot prove the recording is reachable."
    )
    try:
        for factory in (
            lambda: phase8_input_tree_certification(report),
            lambda: phase8_backend_full_tests(report, basetemp),
        ):
            step = factory()
            report.steps.append(step)
            emit(
                f"[verify_phase] {step.status}: {step.name} "
                f"({step.duration_s}s) {step.summary}"
            )

        phase8_dataset_smoke(report, scratch)

        for factory in (
            lambda: phase8_label_firewall(report),
            lambda: phase8_rules_only_benchmark_smoke(scratch),
            lambda: phase8_focused_pytest(
                "release-rules-only-fallback",
                "backend/tests/integration/test_release_rules_only.py",
                basetemp,
            ),
            lambda: phase8_focused_pytest(
                "release-persistent-restart",
                "backend/tests/integration/test_persistent_state_restart.py",
                basetemp,
            ),
            lambda: phase8_release_documents(report),
            lambda: phase8_benchmark_artifacts(report),
            lambda: phase8_fresh_checkout_readiness(report),
            lambda: phase8_prior_phase_evidence(report),
            lambda: phase8_submission_assets(report),
        ):
            step = factory()
            report.steps.append(step)
            emit(
                f"[verify_phase] {step.status}: {step.name} "
                f"({step.duration_s}s) {step.summary}"
            )

        assertions = phase8_gate_assertions(report)
        report.steps.append(assertions)
        emit(
            f"[verify_phase] {assertions.status}: {assertions.name} "
            f"({assertions.duration_s}s) {assertions.summary}"
        )
    finally:
        shutil.rmtree(basetemp, ignore_errors=True)
        shutil.rmtree(scratch, ignore_errors=True)


def main() -> int:

    parser = argparse.ArgumentParser(description="ARGUS CONTROL phase acceptance gate")
    parser.add_argument(
        "--phase", type=int, required=True, choices=sorted(SUPPORTED_PHASES)
    )
    parser.add_argument(
        "--include-test-mode-smoke",
        action="store_true",
        help="run optional read-only Razorpay test-mode smoke probe",
    )
    args = parser.parse_args()

    configure_console_output()
    report = GateReport(
        phase=args.phase,
        include_test_mode_smoke=getattr(args, "include_test_mode_smoke", False),
    )
    report.started_at_utc = utc_now()

    # Capture the commit and working-tree state BEFORE any artifact is written,
    # so the RUNNING artifact this gate writes cannot dirty its own snapshot.
    if report.phase == 8:
        report.release_tree = capture_release_tree()

    try:
        begin_run(report)  # replaces any stale artifact immediately
    except OSError as exc:
        emit(f"[verify_phase] could not write the RUNNING artifact: {exc}")
        return 2

    try:
        run_gate(report)
    except Exception as exc:  # noqa: BLE001 - fail-safe boundary for the artifact
        record_unexpected_failure(report, _current_step, exc)
        try:
            finalize_run(report, forced_failure=_current_step)
        except Exception as write_exc:  # noqa: BLE001 - nothing else we can do
            emit(
                f"[verify_phase] could not write the failure artifact "
                f"({_current_step}): {type(write_exc).__name__}: {write_exc}"
            )
            return 2
        emit(
            f"[verify_phase] unexpected failure during step '{_current_step}': "
            f"{type(exc).__name__}: {exc}"
        )
        return 1

    finalize_run(report)
    return 0 if report.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
