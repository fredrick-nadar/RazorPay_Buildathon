# ARGUS Build Status

Current phase: 5 - Control Room, Approval, Simulated Application, and Audit
Status: PASSED
Last verified commit: gate artifact produced by an independent run on the Phase 5 working tree (pre-commit); suggested commit `feat(control-room): add human approval workflow, simulated correction application, immutable audit trail, and interactive dashboard`
Last evaluation artifact: artifacts/evaluation/phase-05.json

## Implemented

- Database Migration v4 (`backend/app/persistence/migrations.py`):
  - Created `simulated_corrections` table (`correction_id`, `case_id`, `run_id`, `proof_id`, `approval_id`, `target_ledger_entry_id`, `account_code`, `delta_paise`, `applied_at_utc`, `idempotency_key`).
  - Created `approvals` table (`approval_id`, `case_id`, `proof_id`, `reviewer_id`, `action`, `notes`, `approved_at_utc`).
  - Created `audit_log` table (`event_id`, `case_id`, `run_id`, `timestamp_utc`, `actor`, `action`, `payload_json`, `digest`).
- Audit Service (`backend/app/audit/`):
  - `record_audit_event`: Appends tamper-evident audit events with sha256 digests over `(event_id, case_id, run_id, timestamp, actor, action, payload)`.
  - `get_audit_trail`: Retrieves chronological event stream for cases or runs.
  - `verify_audit_completeness`: Validates that every state transition has a cryptographic audit log record.
- Simulated Corrections Application (`backend/app/corrections/application.py`):
  - `apply_simulated_correction`: Enforces strict human approval gate for `APPROVAL_REQUIRED` cases, requires verifier `PASS` proof package, executes dry-run validation, creates linked `SIMULATED_CORRECTION` record in persistence while keeping raw imported source rows 100% immutable.
  - Rejection support: Allows human operators to reject unverified/ambiguous proposals, setting status to `UNRESOLVED` and logging the reviewer justification.
  - Enforces cryptographic idempotency key: Re-applying with identical proof canonical hash returns the existing correction without duplicate insertion.
- REST API Layer (`backend/app/api/`):
  - `routes_runs.py`: `POST /api/v1/runs/reconcile`, `GET /api/v1/runs`, `GET /api/v1/runs/{id}/summary`, `GET /api/v1/runs/{id}/cases`.
  - `routes_cases.py`: `GET /api/v1/cases/{id}` (full case workspace data, hypotheses, proof package, dry-run balance, approvals, and simulated correction), `POST /api/v1/cases/{id}/approve`, `POST /api/v1/cases/{id}/reject`, `GET /api/v1/cases/{id}/audit`.
- Frontend Control Room UI (`frontend/src/app/page.tsx`):
  - Interactive Next.js App Router flight recorder control room:
    - Real-time batch KPI metrics (eligible records, clean match count, exception count, batch status, gross volume in INR).
    - Exception case queue with status and category filtering.
    - Two-column workspace: Case Overview, Competing Hypotheses list, Falsifiable Proof Package banner with cryptographic hash.
    - Interactive Evidence Graph with instant toggle to accessible tabular view.
    - Dry-Run Preview with Before Variance / Proposed Delta / After Variance in formatted ₹ INR.
    - Human Approval Modal with exact delta confirmation and reviewer notes.
    - Chronological Append-Only Audit Timeline drawer.
- Acceptance Gate (`scripts/verify_phase.py`):
  - Added Phase 5 (`SUPPORTED_PHASES = {0, 1, 2, 3, 4, 5}`), chaining regression suites for Phases 0, 1, 2, 3, 4, followed by Phase 5 unit and integration tests (`unit-tests-corrections-application`, `unit-tests-audit-service`, `integration-golden-flow`) and Phase 5 gate assertions.

## Commands and Results

Recorded in `artifacts/evaluation/phase-05.json` (independent run: status PASS, 50 steps, 0 known failures, next_phase 6):

- `.venv\Scripts\python scripts\verify_phase.py --phase 0` - PASS
- `.venv\Scripts\python scripts\verify_phase.py --phase 1` - PASS
- `.venv\Scripts\python scripts\verify_phase.py --phase 2` - PASS
- `.venv\Scripts\python scripts\verify_phase.py --phase 3` - PASS
- `.venv\Scripts\python scripts\verify_phase.py --phase 4` - PASS
- `.venv\Scripts\python scripts\verify_phase.py --phase 5` - PASS (RUNNING-first lifecycle; exit 0)
- Complete Phase 0 step list green: Ruff check, Ruff format-check, strict mypy (55 source files), backend pytest (258 passed), frontend lint/typecheck/Vitest/build, backend and frontend boot probes, secret scan, gitignore coverage.
- Complete Phase 1 step list green: deterministic dataset regeneration, label isolation, dataset tests, gate assertions.
- Complete Phase 2 regression step list green: migration, normalization, reconciliation, benchmark evaluator, integration, adversarial tests, dev/adversarial benchmark reruns.
- Complete Phase 3 regression step list green: scope safety, verifier tests, migration v3, benchmark evaluator v3, dry-run integration, rules-only benchmarks.
- Complete Phase 4 regression step list green: investigator tools, schemas, engine, boundaries, scope safety, dev and adversarial fake agent benchmarks.
- Phase 5 steps green: `unit-tests-corrections-application` (3 passed); `unit-tests-audit-service` (2 passed); `integration-golden-flow` (1 passed); `phase5-gate-assertions` (golden flow PASS, human approval gate enforced, simulated correction idempotent, audit completeness verified, raw source rows immutable).

## Actual Metrics

- Backend tests: 289 passed / 0 failed / 0 skipped.
- Frontend tests: Vitest (2 passed), ESLint (0 errors/warnings), TypeScript (strict zero errors), Next.js production build (100% successful static export).
- End-to-End Golden Flow: complete lifecycle executed (dataset ingestion $\rightarrow$ reconciliation $\rightarrow$ exception detection $\rightarrow$ hypothesis investigation $\rightarrow$ deterministic verifier pass $\rightarrow$ dry-run draft calculation $\rightarrow$ human controller approval $\rightarrow$ simulated correction insertion $\rightarrow$ append-only audit trail logging $\rightarrow$ unresolvable case rejection).
- Financial Safety: signed integer paise only, immutable raw source rows, zero model mutation authority, cryptographic proof hash validation.

## Known Limitations

- Phase 5 uses deterministic and simulated ledger application in SQLite. No production ERP or real payment gateways are connected.
- Phase 5 supports fake and local deterministic AI investigator; live LLM provider integration with external API keys will be configured in Phase 6 / production settings.

## Next Exact Step

Phase 6 - Live Model Provider, Prompt Hardening, and Benchmark Submission.
