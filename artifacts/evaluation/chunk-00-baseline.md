# ARGUS CONTROL — Chunk 0 Trustworthy Baseline

Date: 2026-09-01

Scope: read-only inventory, safety audit, and executable baseline validation

Outcome: **COMPLETE WITH ATTRIBUTED FINDINGS**

This report is not a PRD phase artifact and does not supersede the last passing
Phase 7 gate. `BUILD_STATUS.md` was not changed.

## 1. Governing baseline

- The governing order remains `ARGUS_CONTROL_PRD.md`, frozen MVP contract,
  active phase gate, `ARGUS_CONTROL_MASTER_PROMPT.md`,
  `README_ARGUS_CONTROL.md`, then `AGENTS.md`.
- `Everything.md` is reference-only and is not authoritative.
- No specification document was modified.
- No commit was created.

## 2. Working-tree inventory

### Intended import and Razorpay implementation

Tracked modifications:

- `.env.example` — Groq schema-mapping configuration names only.
- `backend/app/api/routes_ingest.py` — reviewed CSV analysis/commit flow and
  complete-source readiness gate.
- `backend/app/api/routes_razorpay.py` — Test Mode import staging without
  synthetic fallback or immediate gateway-only reconciliation.
- `backend/app/config.py` — Groq configuration and secret aliases.
- `backend/app/importers/razorpay_client.py` — bounded pagination and broader
  read-access checks.
- `backend/app/persistence/migrations.py` — gateway snapshot persistence schema.
- `backend/tests/unit/test_csv_ingest.py` — reviewed mapping, immutable staging,
  readiness, and complete-session tests.
- `backend/tests/unit/test_migration.py` — migration v5 coverage.
- `backend/tests/unit/test_razorpay_adapter.py` — Test Mode credentials,
  pagination, staging, and no-fallback tests.
- `frontend/src/components/connect-dataset-modal.tsx` — three-source import
  wizard and mapping review UI.
- `scripts/seed_razorpay_test_data.py` — Test Mode seed behavior updates.

New untracked implementation files:

- `backend/app/importers/schema_mapping.py` — bounded deterministic/Groq header
  mapping.
- `backend/app/persistence/gateway_imports.py` — immutable gateway snapshot
  repository boundary.
- `backend/tests/unit/test_gateway_imports.py` — gateway persistence tests.

### Intentional OCR removal

The following tracked files are deleted consistently with the approved
CSV-only scope:

- `backend/app/importers/document_extractor.py`
- `backend/app/importers/sandbox_runner.py`
- `backend/tests/unit/test_sandbox_extractor.py`
- `frontend/src/components/sandbox-extraction-studio.tsx`

### Generated-only changes

Fourteen modified files under `artifacts/benchmark/` contain regenerated
runtime timing values. Two modified files under `artifacts/evaluation/` are
generated gate outputs:

- `artifacts/evaluation/phase-06.json` currently records an older failed run.
- `artifacts/evaluation/voice-gate.json` changes measured parse latency only.

These generated changes are not source implementation and must not be treated
as final release evidence until the final benchmark/gate regeneration.

Generated untracked pytest trees:

- `.pytest_tmp_all/`
- `.pytest_tmp_cornerstone/`
- `.pytest_tmp_csv/`
- `.pytest_tmp_unit/`
- `.pytest_tmp_chunk0/`

The four pre-existing trees contained 636 files and approximately 77 MB before
the Chunk 0 run. They include generated synthetic fixtures, evaluator copies,
and SQLite databases. They are not source inputs. They were preserved and not
deleted in Chunk 0.

### Reference-only file

- `Everything.md` is an attached reference snapshot. It includes an unverified
  hard-coded `4,400+ records/second` statement and must not be used as measured
  benchmark evidence.

No other modified or untracked source path was found.

## 3. Safety audit

### Secrets

- Repository secret scan: PASS — no secret-like content found.
- Extended scan of changed/untracked source included Groq, Razorpay, OpenAI,
  GitHub, Slack, private-key, PAN, phone, and card-like patterns.
- The only card-like matches were long zero-filled decimal examples inside
  `Everything.md`; they were not card numbers.
- `.env.local` contains configured local values but is excluded by `.gitignore`
  and is not tracked. Only variable names and SET/EMPTY state were emitted;
  values were never printed.
- `.env.example` contains configuration names only.

Known scanner gap: the built-in secret scanner filters by file extension before
checking stray `.env.*` files, so `.env.local` is not inspected by that gate.
The built-in patterns also do not include Groq `gsk_` credentials. The extended
Chunk 0 scan covered both risks. The scanner should be hardened before release.

### Synthetic-data boundary

- No production Razorpay key, customer identifier, PAN, phone number, card
  number, or real merchant record was found in changed source.
- Test fixtures use fictional identifiers.
- The project remains synthetic-data/Test-Mode-only.

### Label firewall

- `scripts/check_label_isolation.py`: PASS.
- Runtime code cannot reach evaluator-only ground-truth labels.
- Label copies under pytest temporary directories are generated evaluator test
  material, not runtime inputs.

### Hard-coded factual claims requiring later correction

These are pre-existing and outside the current import diff, but they violate the
final dynamic-data objective:

- `backend/app/api/routes_chat.py` supplies a default pending-approval count of
  `6` when the live value is absent.
- `frontend/src/app/page.tsx` contains fixed `100.0%` and `1,880` landing-page
  values.
- `frontend/src/components/executive-dossier-modal.tsx` prints `100.0% Verified`
  independently of the active dossier result.
- `Everything.md` states `4,400+ records/second` without linking it to the
  authoritative current benchmark runner output.

These values must become API-backed measurements or clearly labelled historical
benchmark examples before the final release.

### Current integration gaps confirmed by inspection

- The import UI labels a subsequent upload as `Replace CSV`, while the backend
  immutably appends a distinct file hash to the staged canonical source. The
  current wording and behavior disagree. Chunk 1 must implement explicit active
  source revisions or label the operation as append; it must not silently
  pretend an immutable source was replaced.
- Groq is wired directly into CSV schema mapping, but
  `backend/app/ai/chain.py` does not currently register Groq in the investigator
  provider chain. The existing investigator still resolves through its older
  Gemini/OpenAI/Sarvam/Ollama chain. Chunk 2 must consolidate this provider
  wiring rather than claim that the controller already runs on Groq.

## 4. Executable baseline results

Commands were run against the current working tree without modifying financial
source code.

| Check | Result | Evidence |
|---|---|---|
| Complete backend test suite | PASS | 433 passed, 1 Starlette/httpx deprecation warning |
| Ruff lint | PASS | All checks passed |
| Ruff format check | FAIL | One modified file would be reformatted: `routes_razorpay.py` near line 417 |
| Strict mypy | PASS | No issues in 88 source files |
| Label firewall | PASS | Runtime cannot reach ground-truth labels |
| Frontend ESLint | PASS | No errors reported |
| Frontend strict TypeScript | PASS | `tsc --noEmit` completed |
| Frontend Vitest | PASS | 2 tests passed |
| Frontend production build | PASS | `/`, `/dashboard`, and `/presentation` generated successfully |

The one current failure is formatting-only and belongs to the unfinished
Razorpay import diff. No reconciliation, evidence, verifier, approval,
application, audit, investigator, migration, or frontend behavior test failed.

The modified `phase-06.json` records an earlier frontend build, home-probe, and
Playwright startup failure. The current standalone production build passed, so
the historical build failure is not currently reproducible. Server probes and
Playwright will be rerun in the final authoritative release gate.

## 5. Acceptance decision

- No leaked secrets or real customer data: **PASS**.
- Reconciliation, verifier, approval, correction, and audit behavior healthy:
  **PASS** through the complete 433-test backend suite.
- Every working-tree path classified: **PASS**.
- Current failures attributed: **PASS** — one import-route formatting failure;
  one stale generated Phase 6 failure artifact that did not reproduce in the
  current production build.

Chunk 0 is complete. It establishes a trustworthy baseline; it does not certify
the unfinished import cornerstone as release-ready.

## 6. Frozen three-day scope

Must ship, in order:

1. CSV/Test Mode import cornerstone.
2. One durable reconciliation controller using the existing bounded
   investigator and deterministic core.
3. Dynamic controller state and evidence in the dashboard.
4. Telegram as a thin intake/monitoring channel.
5. Full regression, benchmark regeneration, deployment smoke test, and
   five-minute demo package.

Explicitly excluded from the three-day build:

- OCR and image ingestion.
- PDF and XLSX ingestion.
- WhatsApp integration.
- Additional model agents or multi-agent orchestration.
- Production Razorpay data or credentials in fixtures.
- Real money movement or production ERP mutation.
- New voice capabilities.

## 7. Chunk 1 entry conditions

Before Chunk 1 can pass:

- Format the modified Razorpay route.
- Correct and test immutable append/replacement semantics.
- Harden the secret scan for `.env.*` and Groq key patterns.
- Remove or defer generated artifact noise from the implementation review.
- Test Razorpay pagination, unavailable settlements, Groq failure, malformed
  rows, duplicate imports, and complete-source readiness.
