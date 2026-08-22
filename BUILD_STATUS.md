# ARGUS Build Status

Current phase: 0 — Foundation and Frozen Contracts
Status: PASSED
Last verified commit: Phase 0 baseline commit `chore(core): establish reproducible argus foundation` (the artifact's environment block records the pre-commit HEAD 3b1903546550f1c02a84ccf9e8b5881c198925a0)
Last evaluation artifact: artifacts/evaluation/phase-00.json

## Implemented

- Backend skeleton (`backend/`): FastAPI app factory; `GET /api/v1/health` (version + SQLite persistence status) and `GET /api/v1/version`; pydantic-settings configuration with safe defaults and rules-only startup (no model key required to start); 14 frozen domain enums; integer-paise money helpers that reject floats and bools; SQLite persistence boundary (stdlib sqlite3 behind a repository protocol, `app_meta` table, thread-safe).
- Frontend skeleton (`frontend/`): Next.js App Router + React + strict TypeScript + Tailwind CSS; placeholder control-room page with no numeric metrics; TypeScript domain enums mirrored from Python.
- Frozen cross-language contract: `contracts/domain_enums.json` generated only by `scripts/generate_domain_contracts.py`; Python and Vitest tests compare against it read-only (no test writes it).
- Tooling: backend pytest / ruff / mypy (strict) via `.venv`; frontend eslint / tsc / vitest / Playwright via npm; pinned `backend/requirements.lock.txt`; committed `frontend/package-lock.json`.
- `scripts/verify_phase.py`: authoritative Phase 0 gate; installs nothing (bootstrap is separate, per AGENTS.md §7); dependency preflight fails fast with a bootstrap hint; probes live uvicorn and `next start` servers; secret scan; gitignore coverage; writes `artifacts/evaluation/phase-00.json` on both pass and fail.
- `.env.example` (names only), `AGENTS.md` (permanent project rules), `.gitignore` verified unchanged.

## Commands and Results

Recorded in `artifacts/evaluation/phase-00.json` (latest = independent runner verification of the same code):

- `.venv\Scripts\python scripts\verify_phase.py --phase 0` — **PASS on the independent runner** (started 2026-08-22T11:50:36Z, finished 2026-08-22T11:52:03Z; RUNNING artifact written first, FAIL-safe lifecycle)
- All 12 mandatory steps PASS: both preflights; backend ruff check / ruff format (18 files) / mypy strict (10 files) / pytest; frontend lint / typecheck / vitest / next build; backend boot health (`GET /api/v1/health` → 200, persistence sqlite ok, api v1); **frontend boot probe passed** (`next start` + `GET /` → 200 with ARGUS CONTROL heading); secret-scan; gitignore-coverage
- Backend pytest: **65 passed, 0 failed, 0 skipped** from a fresh unique `--basetemp tmp/pytest-phase-00-hjsy5n24` with `-p no:cacheprovider` (no fixed temp reused)
- Optional Playwright smoke on the independent runner: **timed out at 60 seconds** and was correctly recorded as non-blocking — process tree terminated, entry in `known_failures`, `failed_step: null`, gate status unaffected
- Playwright smoke **PASSED (1 test each)** in the two preceding ZCode runs of the identical code (finished 2026-08-22T11:48:04Z and 2026-08-22T11:48:51Z, each with its own unique basetemp)

## Actual Metrics

- Backend unit tests: 55 domain + 10 verifier-safety regression = **65 passed, 0 failed, 0 skipped** (final artifact finished 2026-08-22T11:52:03Z)
- Frontend unit tests: 2 passed; Playwright smoke: PASSED in the two preceding ZCode runs, timed out at the 60-second cap (recorded non-blocking) on the independent runner
- Gate step durations: see artifact (frontend-build 12.68s was the longest mandatory step on the final run)
- Reconciliation/benchmark metrics: NOT_MEASURED (first measured in Phase 1+)

## Known Limitations

- Phase 0 baseline is committed as `chore(core): establish reproducible argus foundation`. The optional Playwright smoke can exceed its 60-second cap on slower runners; by design this is recorded as a non-blocking failure and never gates Phase 0
- Long-running shells may carry a stale PATH from before the Git install; use a fresh terminal or the absolute path `C:\Program Files\Git\cmd\git.exe` if bare `git` is not found (AGENTS.md §3)
- Playwright e2e is informational for Phase 0; the acceptance gate does not block on it
- The evaluator must use the venv Python for PRD-documented commands (`python -m pytest ...`), or activate `.venv` first
- SQLite schema is Phase-0 minimal (`app_meta` only); domain tables arrive with later phases
- No CI yet (optional per PRD; can be added on top of the Phase 0 commit)

## Next Exact Step

Phase 1 — Synthetic Data, Ground Truth, and Isolation (PRD §16): deterministic generators for payments/refunds/settlements/bank/ledger, four exception injectors, 100+ record dev dataset, adversarial dataset, manifests with hashes, `check_label_isolation.py`, and the Phase 1 acceptance gate via `scripts/verify_phase.py --phase 1` (to be extended).
