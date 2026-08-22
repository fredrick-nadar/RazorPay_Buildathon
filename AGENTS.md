# AGENTS.md — ARGUS CONTROL Permanent Project Rules

Binding operational rules for any coding agent or human contributing to this
repository. These rules summarize and operationalize the governing documents;
they never override them.

## 1. Governing documents and precedence

Governing documents, in precedence order when they disagree (PRD §0.3):

1. Safety and financial integrity rules in `ARGUS_CONTROL_PRD.md`.
2. Frozen MVP contract in the PRD.
3. Current phase acceptance gate in the PRD.
4. `ARGUS_CONTROL_MASTER_PROMPT.md` implementation guidance.
5. `README_ARGUS_CONTROL.md` narrative.
6. This file.

Never edit the three specification documents (`README_ARGUS_CONTROL.md`,
`ARGUS_CONTROL_PRD.md`, `ARGUS_CONTROL_MASTER_PROMPT.md`) as part of normal
phase work. `BUILD_STATUS.md` is the living status file and is updated only
after a phase gate passes.

## 2. Project identity

ARGUS CONTROL is a financial flight recorder for merchant reconciliation
(Razorpay AI Buildathon 2026, Track 04). It deterministically reconciles
synthetic payment, refund, settlement, bank, and ledger records, uses one
bounded AI investigator only for residual exceptions, verifies every proposed
explanation with deterministic code, previews ledger corrections as dry-runs,
requires human approval, and leaves ambiguous cases unresolved.

Product principle: **rules for calculation, AI for investigation, verification
for closure, approval for authority, humans for ambiguity.**

## 3. Environment

- Windows host; commands run from the repository root in cmd or PowerShell.
- Python 3.12 available as `python`; backend virtualenv lives at `.venv`
  (gitignored). Use `.venv\Scripts\python.exe` for backend tooling.
- Node.js 22 and npm 11 available; frontend uses npm (not pnpm).
- **git is installed** (git 2.55.0.windows.5, reconfirmed 2026-08-22 at
  `C:\Program Files\Git\cmd\git.exe`, on branch `main` tracking
  `origin/main`). Note: shells spawned by long-running tools may carry a
  stale PATH from before the Git install; if bare `git` is not found, use the
  absolute path `C:\Program Files\Git\cmd\git.exe` or a fresh terminal.

## 4. Stack (frozen for the mandatory build)

- Backend: Python 3.12, FastAPI, Pydantic v2, pydantic-settings; stdlib
  `sqlite3` behind a repository boundary. SQLite is the zero-configuration
  default database.
- Frontend: Next.js App Router, React, strict TypeScript, Tailwind CSS; npm.
- Backend tests: pytest. Frontend tests: Vitest (unit) and Playwright (E2E).
- Authoritative phase gate: `python scripts/verify_phase.py --phase N`
  (run it with the venv Python).

**Not on the Phase 0 mandatory path:** PostgreSQL, Drizzle ORM, Zod, pnpm,
shadcn/ui, and fast-check. They are not permanently rejected; they may be
reconsidered later only if the active PRD phase permits them and the project
owner approves. Redis, pgvector, RAG/vector memory, distributed workers,
blockchain, microservices, and additional model agents are out of scope for
the mandatory MVP path per the PRD.

## 5. Financial safety restrictions (never violate)

1. INR money is signed **integer paise**. Never use binary floating-point for
   money: `app.domain.money.require_paise` rejects floats and bools, and
   decimal rupee strings are parsed with exact string arithmetic.
2. Timestamps are stored in UTC; the merchant timezone is separate. Source
   event time, settlement time, import time, and accounting date stay distinct.
3. Imported source rows are immutable; normalized rows keep a pointer and
   content hash to the source row.
4. Imports, runs, approvals, and simulated application are idempotent.
5. Never silently drop an input row; malformed rows are quarantined.
6. Ground-truth labels are evaluator-only. Runtime code must never import,
   mount, query, or serialize anything under `datasets/**/labels/`.
7. The AI model never performs deterministic reconciliation, never calculates
   authoritative totals, never marks a case resolved, never approves or applies
   corrections, and never writes financial tables. There is no model-callable
   `approve`, `apply`, `update_ledger`, or `mark_resolved` tool.
8. Every resolution requires a deterministic verifier `PASS` with cited
   evidence IDs and rule versions. Ambiguity cannot be overridden by model
   confidence; ambiguous cases stay `UNRESOLVED`.
9. Corrections are dry-run first; every nonzero ledger delta requires human
   approval in the MVP; application only creates a new linked
   `SIMULATED_CORRECTION` entry — imported entries are never edited.
10. Audit events are append-only. Voice (if ever built) is never an approval
    channel and never applies corrections.
11. Synthetic data only. Never use real personal, customer, merchant, bank,
    card, UPI, or production data. Policy values in the demo are labelled
    synthetic merchant policy, not Razorpay policy.
12. Secrets never enter fixtures, logs, prompts, screenshots, or source
    control. `.env.example` contains names only.
13. No prototype action moves real money or writes a production ERP. Razorpay
    integrations, if any, use documented Test Mode or public read APIs only.
14. Never publish a benchmark number that was not produced by the benchmark
    runner. No placeholder metric may look like a measured result. Failed and
    unresolved cases stay in evaluation denominators.

## 6. Architecture boundaries

- Authoritative money, matching, verification, proof, dry-run, authority, and
  audit logic lives only in `backend/app` domain services.
- API handlers stay thin (`backend/app/api`).
- UI components contain no financial truth logic; they render API results.
- All persistence flows through the SQLite boundary in
  `backend/app/persistence`; domain code does not open connections directly.
- `contracts/domain_enums.json` is generated **only** by
  `scripts/generate_domain_contracts.py` and committed. Python and TypeScript
  tests treat it as read-only. To change an enum: update both enum modules,
  rerun the generator explicitly, and keep tests green.
- `scripts/verify_phase.py` never installs or downloads dependencies; it fails
  fast with a bootstrap hint if dependencies are missing.

## 7. Bootstrap (fresh install) — separate from the verifier

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -r backend/requirements.lock.txt
cd frontend
npm ci
cd ..
```

(`npm ci` requires the committed `package-lock.json`; the first-time lock was
created with `npm install`.)

Optional, for Playwright E2E (run inside `frontend/`):
`cd frontend && npx playwright install chromium`. E2E is not part of the
Phase 0 gate; the verifier records it as skipped when browsers are absent.

## 8. Validation commands (Phase 0 authoritative set)

Run with the venv Python unless noted:

```bat
.venv\Scripts\python -m pytest backend/tests/unit -q
.venv\Scripts\python -m ruff check backend
.venv\Scripts\python -m ruff format --check backend
cd backend && ..\.venv\Scripts\python -m mypy & cd ..
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run test
npm --prefix frontend run build
.venv\Scripts\python scripts/verify_phase.py --phase 0
```

`verify_phase.py` re-runs the mandatory set, boots both servers to probe
`/api/v1/health`, `/api/v1/version`, and `/`, runs the secret scan, and writes
`artifacts/evaluation/phase-00.json`. Its exit code is the gate.

## 9. Phase protocol

Follow PRD §16 and the Master Prompt §8:

1. Implement only the smallest coherent slice for the current phase.
2. Add tests with the implementation.
3. Run the phase commands; fix failures without expanding scope.
4. Generate `artifacts/evaluation/phase-NN.json` from actual command results.
5. Update `BUILD_STATUS.md` (Master Prompt §14 format) **only after tests
   pass**, linking the artifact.
6. Never begin the next phase when a mandatory gate fails.
7. Do not implement later-phase features early. Optional work is prohibited
   until Phase 7 passes.

## 10. Git and change policy

- Commit only when the user explicitly authorizes it; otherwise provide the
  suggested commit message from the PRD phase section.
- Preserve user-authored files and unrelated working-tree changes.
- Before finishing any phase: review the diff (`git diff`, `git status`)
  for secrets, placeholder metrics, TODO/FIXME drift, imports from label
  directories, generated caches, local databases, and unintended scope.
- `.zcode/` (local agent plans) is gitignored and must never be committed.

## 11. Data and evaluation rules

- Datasets live under `datasets/{dev,holdout,adversarial}` with `inputs/`,
  `labels/`, and `manifest.json`; only evaluation processes touch `labels/`.
- The submission benchmark targets at least 500 deterministic fictional
  records (never fewer than the track minimum of 50) modeled after Razorpay's
  public report schemas, with match rate, measured accuracy, throughput, and
  an honest unresolved-exception list.
- Anti-overfitting rules in PRD §13.3 apply (label firewall, separate seeds,
  frozen holdout, documented limitations).
