# ARGUS Build Status

Last passed phase: 7 - Frozen Holdout Benchmark and Hardening. Status: PASSED
Current phase: 8 - Submission Release. Status: IN PROGRESS / PENDING RELEASE ASSETS

Correction (2026-09-05): this file previously recorded "Current phase: 8 ... Status:
PASSED". That claim was unsupported and has been withdrawn. At the time it was
written `scripts/verify_phase.py` accepted only phases 0-7 and
`artifacts/evaluation/phase-08.json` did not exist, so no Phase 8 gate had ever
been executed. Phase 8 is now executable and its artifact is generated from real
command outcomes; it will remain FAIL until the owner supplies the release
assets listed under "Phase 8 - Outstanding Owner Actions".

Last verified commit: gate artifacts produced by independent runs on the Phase 7
working tree (pre-commit)
Last evaluation artifact: artifacts/evaluation/phase-07.json
(+ artifacts/evaluation/voice-gate.json, artifacts/evaluation/phase-08.json for
the current Phase 8 attempt)

## Implemented

- ARGUS Enterprise Reconciliation Extensions (Track 04 Enhancements):
  - Executive Audit Dossier (`backend/app/api/routes_runs.py`, `frontend/src/components/executive-dossier-modal.tsx`): one-click printable & exportable compliance dossier with cryptographic SHA-256 batch proof seal, full transaction lineage traces, verifier rule citations, auditor sign-offs, and append-only audit event digest.
  - MDR & GST Pricing Reconciler (`backend/app/domain/fee_audit.py`, `frontend/src/components/fee-audit-card.tsx`): automated gateway fee deduction and GST rate audit down to exact signed integer paise (zero floats) against contractual merchant rate cards (2.00% MDR + 18% GST), detecting silent micro-leakages.
  - Multi-Source CSV File Drop Zone (`backend/app/api/routes_ingest.py`, `frontend/src/components/connect-dataset-modal.tsx`): drag-and-drop ingestion for external Razorpay settlement CSVs, bank statement CSVs, and merchant ledger CSVs with column aliasing, SHA-256 checksum validation, and instant batch reconciliation.
- ARGUS Voice Control Layer (`backend/app/voice/`, PRD 13.5 - optional work under its own gate, Phase 7 passed first):
  - Deterministic parser (`parser.py`): pure regex + keyword classification over a normalized transcript - no model in the parse path, prompt-injection inert. Forbidden patterns are always evaluated before allowed patterns.
  - Indian number converter: digits, `10 thousand`, `5 lakh`, `2 crore`, `50 paise`, Devanagari digits and Hindi number words -> exact signed integer paise (no float arithmetic).
  - Guardrails (`guardrails.py`): the complete authority boundary. All 8 forbidden families (approve, apply, edit record, override verifier, mark resolved, move money, change policy, reveal secret) produce localized refusals directing the controller to the visible approval panel. Defense in depth: guardrails re-run at execution time.
  - Executor (`executor.py`): read-only actions only - show/explain cases, list unresolved, filter by status/category/amount, missing-evidence briefing, list existing DRAFT previews, idempotent dev-batch run (confirmation-gated), navigate to the fixed `/presentation` route. No approve/apply/edit/move capability exists in the module.
  - Service + API (`service.py`, `api.py`): server-issued opaque execution tokens (client-echoed intent labels are impossible); TTL cache; confirmation gate for RUN_RECONCILIATION and PREPARE_VERIFIED_CORRECTION_PREVIEWS; audit events `VOICE_COMMAND_EXECUTED` / `VOICE_COMMAND_REFUSED` with 200-char minimized transcripts; no audio ever reaches the backend.
  - Endpoints: `POST /api/v1/voice/parse`, `POST /api/v1/voice/execute`, `GET /api/v1/voice/languages` (honest per-language labels: en-IN/hi-IN ARGUS_TESTED; ta/te/kn AVAILABLE_FROM_PROVIDER until their packs pass).
  - Honest case resolution: spoken case references resolve literally; unknown IDs return an explicit NOT_FOUND - never a guess.
- Frontend Voice Copilot (`frontend/src/components/voice-controller.tsx`):
  - Push-to-talk (Web Speech API, on-device recognition - only text is sent) with live transcript preview and animated listening state.
  - Language selector with honest capability labels; typed command bar fallback (Ctrl+Shift+V); browser TTS playback (visible text is authoritative).
  - Distinct refusal banner, confirmation step for run/previews, mic-denied / no-speech / unsupported-browser states; voice failures never interrupt a running reconciliation.
  - Rendered in the control room (`/dashboard`); zero approve/apply affordance exists in the component.
- Presentation Mode (`frontend/src/app/presentation/page.tsx`):
  - Full-screen flight-recorder telemetry for the demo, live from the runs API (no hard-coded metrics), fixed route for OPEN_PRESENTATION_MODE.

- Frozen Holdout Dataset (`datasets/holdout/`):
  - 100 reconciliation windows, 1,880 total rows (740 payments, 50 refunds, 100 settlements, 100 bank entries, 890 ledger entries), 23 exception cases (6 duplicate ledger postings, 6 missing refund postings, 6 settlement timing shifts, 5 ambiguous evidence pairs).
  - Generated deterministically from seed 9107 through the evaluator-only `--unfreeze-holdout` path, then reshaped by the independent holdout variation transform (`backend/app/evaluation/holdout_variation.py`: deterministic row shuffle, harmless column-name variants resolved by ingest aliases, optional-field subset drops) per PRD 13.3 anti-overfitting rules; strict label firewall enforcement throughout.
- Hardening & Edge-Case Battery (`backend/tests/hardening/test_hardening_battery.py`):
  - Empty database and empty input files: zero crashes, zero variance, clean exit.
  - Scale volume performance: the 1,880-row batch reconciles in under 0.2 s. The measured throughput figure is not restated here; it is published once, from `artifacts/benchmark/final.json`, under Actual Metrics.
  - Corrupted payloads and malformed timestamps: safely quarantined without unhandled exceptions.
  - Model timeout and provider gateway failure safety: unhandled model failures fallback strictly to `FAILED`/`UNRESOLVED` without producing `RESOLVED` or modifying ledgers.
  - Adversarial prompt injection defense: free-form text attacks inside AI explanations are completely blocked by the deterministic mathematical verifier.
  - Stale proof and approval rejection: unverified cases or missing proofs cannot be approved; rejections cleanly transition to `REJECTED`/`UNRESOLVED`.
- Machine-Generated Benchmark Artifacts (`artifacts/benchmark/`):
  - `artifacts/benchmark/final.json`: Comprehensive frozen-denominator evaluation report covering precision, match rate, case accuracy, false pass count, dry-run error, proof completeness, throughput, and re-run idempotency hash.
  - `artifacts/benchmark/final_summary.md`: Human-readable executive summary with explicit numerator/denominator accounting and honest unresolved exception inventory.
- Phase 7 Authoritative Acceptance Gate (`scripts/verify_phase.py`):
  - Added Phase 7 (`SUPPORTED_PHASES = {0, 1, 2, 3, 4, 5, 6, 7}`), chaining regression suites for Phases 0 through 6, followed by holdout generation, label isolation check, hardening test suite, holdout rules-only benchmark, holdout fake agent benchmark, and Phase 7 gate assertions ($\ge 500$ eligible records, precision 1.0, case accuracy 1.0, 0 false passes, 0 dry-run error, 100% proof completeness).

## Commands and Results

Recorded in `artifacts/evaluation/phase-07.json` (independent run: status PASS, 61 steps, 0 known failures, exit code 0):

- `.venv\Scripts\python scripts\verify_phase.py --phase 0` - PASS
- `.venv\Scripts\python scripts\verify_phase.py --phase 1` - PASS
- `.venv\Scripts\python scripts\verify_phase.py --phase 2` - PASS
- `.venv\Scripts\python scripts\verify_phase.py --phase 3` - PASS
- `.venv\Scripts\python scripts\verify_phase.py --phase 4` - PASS
- `.venv\Scripts\python scripts\verify_phase.py --phase 5` - PASS
- `.venv\Scripts\python scripts\verify_phase.py --phase 6` - PASS
- `.venv\Scripts\python scripts\verify_phase.py --phase 7` - PASS (RUNNING-first lifecycle; exit 0)
- Complete Phase 0 step list green: Ruff check, Ruff format-check, strict mypy (60 source files), backend pytest (270 passed), frontend lint/typecheck/Vitest/build, backend and frontend boot probes, secret scan, gitignore coverage.
- Complete Phase 1 step list green: deterministic dataset regeneration, label isolation, dataset tests, gate assertions.
- Complete Phase 2 regression step list green: migration, normalization, reconciliation, benchmark evaluator, integration, adversarial tests, dev/adversarial benchmark reruns.
- Complete Phase 3 regression step list green: scope safety, verifier tests, migration v3, benchmark evaluator v3, dry-run integration, rules-only benchmarks.
- Complete Phase 4 regression step list green: investigator tools, schemas, engine, boundaries, scope safety, dev and adversarial fake agent benchmarks.
- Complete Phase 5 regression step list green: corrections application, audit service, golden flow integration, phase 5 gate assertions.
- Complete Phase 6 regression step list green: failure injector, razorpay adapter, event failures adversarial, failure lab benchmark, phase 6 gate assertions.
- Phase 7 steps green: `dataset-generate-holdout` (1,880 rows); `check-label-isolation-holdout` (PASS); `unit-tests-hardening-battery` (10 passed); `benchmark-rules-only-holdout` (PASS); `benchmark-final-agent-holdout` (PASS); `phase7-gate-assertions` (holdout >=500 records verified [1,880 records], precision 1.0, 0 false passes, 0 dry-run error, proof completeness 18/18, 100% audit completeness, final benchmark artifacts written).

## Actual Metrics

- Backend tests: 412 passed / 0 failed / 0 skipped (includes 72 voice-layer + 17 AI-chain/agent tests).
- Voice acceptance gate (`artifacts/evaluation/voice-gate.json`, version argus-voice-gate-v1): allowed-intent accuracy 45/45 = 1.0; entity extraction accuracy 14/14 = 1.0; unsafe-command refusal rate 28/28 = 1.0; false execution count 0; median parse latency 0.062 ms. Gate PASSED.
- Frontend tests: Vitest (2 passed), ESLint (0 errors), TypeScript (strict zero errors), Next.js production build (3 routes: /, /dashboard, /presentation), Playwright E2E (2 passed).
- AI investigator provider chain (ackend/app/ai/, PRD 10): Gemini -> OpenAI -> Sarvam-M -> Ollama (local Llama, opt-in via ARGUS_OLLAMA_ENABLED) -> deterministic fake fallback. Agentic tool-calling loop (investigator/llm_provider.py) enforces the existing allowlist/budget/validation contract; per-case model+tool traces persist into run summaries; /api/v1/ai/status exposes honest engine availability. Provider id recorded per run.
- Phase 7 regression re-run after voice integration: PASS (precision 1.0, 0 false passes, proof completeness 18/18, idempotent replay).
- Frontend tests: Vitest (2 passed), ESLint (0 errors/warnings), TypeScript (strict zero errors), Next.js production build (100% successful static export).
- Holdout Dataset Scale: 1,880 eligible records (exceeding $\ge 500$ benchmark target and 50 track minimum).
- Match Precision: 100.0% (1,124 / 1,124 explicit matches).
- Record Match Rate: 99.15% (1,864 / 1,880 records).
- Case Classification Accuracy: 100.0% (23 / 23 cases).
- False Verifier Passes: 0 (0 / 23).
- Money-Weighted Dry-Run Error: ₹0.00 (0 paise).
- Proof Package Completeness: 18 / 18 (100%).
- Ambiguous Case Escalation: 5 / 5 (100% preserved as UNRESOLVED).
- Throughput: 9,656.10 rec/s (the value recorded in the committed `artifacts/benchmark/final.json` by the Phase 7 holdout agent-mode run; historical committed evidence, not a fresh release measurement). A previous revision of this file published 9,859.51 rec/s, which no committed artifact supported; that figure is withdrawn.
- Replay Idempotency Hash: `69c89e71cbf7b7cc39934069f0962bb3837d2a7c3fae7c275a8eedd7b476f31e`.
- Duplicate Ledger Adjustments: 0 (measured by replay diagnostics across independent replay databases; recorded in `artifacts/benchmark/final.json` → `replay_diagnostics`).

## Phase 8 - Submission Release (IN PROGRESS)

Implemented in this release-engineering slice (software side):

- Validated browser-origin policy (`backend/app/cors.py`): safe localhost
  defaults, explicit production origins, wildcard never combined with
  credentials, malformed origins rejected, deterministic normalization,
  non-secret state in `safe_summary()`.
- Enforced persistence contract: `ARGUS_DB_PATH` and `ARGUS_IMPORT_STAGING_ROOT`
  validated; startup creates only the required parent directories; automatic,
  ordered, idempotent migration; offline restart/restore integration proof.
- Documented Telegram deployment boundary (one backend process; competing
  pollers under multiple workers) with tests proving no network access when
  disabled and channel-only degradation on failure.
- Rules-only availability release check with armed, non-vacuous outbound
  tripwires on every real transport boundary.
- Release documentation: `README.md`, `docs/architecture.md`,
  `docs/data-flow.md`, `docs/security-and-deployment.md`.
- Executable Phase 8 gate in `scripts/verify_phase.py`
  (`SUPPORTED_PHASES = {0..8}`) writing `artifacts/evaluation/phase-08.json`
  from actual command outcomes.

Hardened in the Codex correction pass:

- Release-asset validation (`scripts/release_assets.py`): stdlib structural
  parsing of PNG/JPEG and of the ISO base media container, owner-recorded
  SHA-256 matching, minimum sizes and dimensions, repository path confinement,
  `urlsplit`-based hosted-URL rules, and distinct primary/backup locations and
  content hashes. Fabricated media no longer passes.
- Release identity: Phase 8 captures the commit and working tree BEFORE writing
  its artifact and fails unless the input tree matches that commit, with only
  its own `phase-08.json` exempt. Fresh-checkout readiness now requires every
  release-critical runtime module, test, config, lockfile, document and
  referenced release asset to be tracked.
- Benchmark evidence (`scripts/release_evidence.py`): the real Phase 7 contract
  (benchmark version, frozen holdout, mode/provider, complete evaluation
  sections, gate conditions, replay idempotency, unresolved inventory) is
  enforced, `final_summary.md` must be derived from `final.json`, and published
  figures in README/BUILD_STATUS must agree with the artifact. Phase 7 and
  Phase 8 share one evaluator (`phase7_core_conditions`).
- Committed evaluation artifacts no longer record this machine's absolute
  checkout path; it is redacted to `<repo>`.

## Phase 8 - Outstanding Owner Actions

Phase 8 is expected to FAIL until these exist. They are owner-supplied
evidence, not software defects, and the gate must not be weakened to pass:

- Primary demo video recording.
- Backup demo video recording.
- Application screenshots whose displayed values are traceable to committed
  benchmark artifacts.
- `artifacts/release/submission-manifest.json` describing the above (schema in
  `docs/security-and-deployment.md`, section 7).
- Owner live acceptance of the Telegram channel with a real bot.
- Final release benchmark rerun on the frozen holdout, republished from the
  regenerated artifacts.
- Commit this release-engineering slice; until then Phase 8 correctly fails
  input-tree certification and fresh-checkout readiness.
- Fresh-clone rehearsal and the submission tag (PRD Phase 8 steps 10-12).

## Known Limitations

- Live external LLM provider API keys remain optional and separate from offline deterministic fake provider benchmark tests.
- Razorpay Test Mode client is read-only and synthetic offline-first; no live real money movements or production ERP mutation capabilities exist.
- The ARGUS Voice Control Layer (optional work #3, PRD 18) is implemented and gated; multilingual Tier-1 expansion (ta/te/kn demo-ready) requires per-language test packs before those languages may be labelled ARGUS_TESTED.
- Telegram is offline-verified only; a multi-worker backend deployment is unsupported while it is enabled.
