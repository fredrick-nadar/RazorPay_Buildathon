"""Phase acceptance gate runner (authoritative per PRD section 16).

Usage:

    .venv\\Scripts\\python scripts\\verify_phase.py --phase 0
    .venv\\Scripts\\python scripts\\verify_phase.py --phase 1

Phase 1 runs the complete, unchanged Phase 0 step list first and then appends
the dataset steps; Phase 0 can never be weakened by a later phase gate.

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

SUPPORTED_PHASES = {0, 1}

PHASE_NAMES = {
    0: "Foundation and Frozen Contracts",
    1: "Synthetic Data, Ground Truth, and Isolation",
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
                "command": s.command,
                "status": s.status,
                "duration_s": s.duration_s,
                "summary": s.summary,
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


def record_unexpected_failure(report: GateReport, step: str, exc: BaseException) -> None:
    report.known_failures.append(
        f"unexpected exception during step '{step}': {type(exc).__name__}: {exc}"
    )


def finalize_run(
    report: GateReport,
    artifact_dir: Path = ARTIFACT_DIR,
    forced_failure: str | None = None,
) -> Path:
    report.finished_at_utc = utc_now()
    blocking_failures = [s for s in report.steps if s.status == "FAIL" and s.gate_blocking]
    for step in report.steps:
        if step.status == "FAIL" and not step.gate_blocking:
            report.known_failures.append(f"non-blocking failure: {step.name}")
    report.status = "FAIL" if (forced_failure is not None or blocking_failures) else "PASS"
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
            name, display, "FAIL", duration, f"timed out after {timeout_s}s", gate_blocking
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
    request = urllib.request.Request(url, headers={"User-Agent": "argus-verify-phase/0"})
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
        if path.suffix.lower() not in SCAN_EXTENSIONS and path.name != ".gitignore":
            continue
        if path.name == ".env" or (
            path.name.startswith(".env.") and path.name != ".env.example"
        ):
            findings.append(f"stray env file: {relative}")
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
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    if stripped.split("=", 1)[1].strip():
                        findings.append(f".env.example has a non-empty value: {relative}")

    status = "PASS" if not findings else "FAIL"
    summary = "no secret-like content found" if not findings else "; ".join(findings[:5])
    return StepResult("secret-scan", "repository secret scan", status, 0.0, summary)


def check_gitignore_coverage(report: GateReport) -> StepResult:
    set_current_step("gitignore-coverage")
    gitignore = REPO_ROOT / ".gitignore"
    if not gitignore.is_file():
        return StepResult(
            "gitignore-coverage", "check .gitignore patterns", "FAIL", 0.0, ".gitignore missing"
        )
    text = gitignore.read_text(encoding="utf-8")
    missing = [p for p in REQUIRED_GITIGNORE_PATTERNS if p not in text]
    status = "PASS" if not missing else "FAIL"
    summary = (
        "required ignore patterns present" if not missing else f"missing patterns: {missing}"
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
            return (completed.stdout or completed.stderr or "").strip().splitlines()[-1][:100]
        except (OSError, subprocess.SubprocessError):
            return "unavailable"

    node_cmd = ["cmd", "/c", "node", "--version"] if IS_WINDOWS else ["node", "--version"]
    git_cmd = find_git()
    if git_cmd is None:
        git_detail = "unavailable (not on PATH and not at the standard install location)"
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


# ---------------------------------------------------------------------------
# Gate orchestration.
# ---------------------------------------------------------------------------


def run_gate(report: GateReport) -> None:
    collect_environment(report)
    emit(f"[verify_phase] phase {report.phase} gate started {report.started_at_utc}")

    if not run_phase0_steps(report):
        return
    if report.phase == 1:
        run_phase1_steps(report)


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
            ("frontend-typecheck", npm_args("--prefix", "frontend", "run", "typecheck"), 300),
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
            if (generated / relative).read_bytes() != (committed / relative).read_bytes():
                problems.append(f"bytes differ: {relative}")
    duration = round(time.perf_counter() - started, 2)
    status = "PASS" if not problems else "FAIL"
    summary = (
        "byte-identical regeneration of inputs, labels, and manifests"
        if not problems
        else "; ".join(problems[:5])
    )
    return StepResult(name, f"compare {generated} vs {committed}", status, duration, summary)


def load_backend_evaluation() -> None:
    """Make the evaluator-only backend package importable for assertions."""
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))


def collect_phase1_metrics_and_violations() -> tuple[list[str], dict[str, object]]:
    """Evaluator-side acceptance assertions over the committed datasets."""
    load_backend_evaluation()
    from app.evaluation import control_totals as ct

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
        violations.extend(f"{profile}: {problem}" for problem in ct.root_manifest_violations(root))
        violations.extend(
            f"{profile}: {problem}" for problem in ct.labels_manifest_violations(root)
        )
        labels = json.loads((root / "labels" / "labels.json").read_text(encoding="utf-8"))
        labels_manifest = json.loads(
            (root / "labels" / "manifest.json").read_text(encoding="utf-8")
        )
        root_manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        seeds_in_use.add(int(root_manifest["seed"]))
        dataset_metrics[profile] = {
            "rows_by_file": {
                relative: info["rows"] for relative, info in root_manifest["files"].items()
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
            violations.append("holdout: seed must exist and differ from dev/adversarial")
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
    scratch = Path(tempfile.mkdtemp(prefix="verify-phase-01-datasets-", dir=str(TMP_DIR)))
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


def main() -> int:
    parser = argparse.ArgumentParser(description="ARGUS CONTROL phase acceptance gate")
    parser.add_argument("--phase", type=int, required=True, choices=sorted(SUPPORTED_PHASES))
    args = parser.parse_args()

    configure_console_output()
    report = GateReport(phase=args.phase)
    report.started_at_utc = utc_now()

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
