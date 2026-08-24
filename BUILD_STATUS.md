# ARGUS Build Status

Current phase: 7 - Frozen Holdout Benchmark and Hardening
Status: PASSED
Last verified commit: gate artifact produced by an independent run on the Phase 7 working tree (pre-commit); suggested commit `feat(benchmark): complete phase 7 frozen holdout benchmark and hardening`
Last evaluation artifact: artifacts/evaluation/phase-07.json

## Implemented

- Frozen Holdout Dataset (`datasets/holdout/`):
  - 100 reconciliation windows, 1,880 total rows (740 payments, 50 refunds, 100 settlements, 100 bank entries, 890 ledger entries), 23 exception cases (6 duplicate ledger postings, 6 missing refund postings, 6 settlement timing shifts, 5 ambiguous evidence pairs).
  - Generated deterministically from seed 9107 through the evaluator-only `--unfreeze-holdout` path, then reshaped by the independent holdout variation transform (`backend/app/evaluation/holdout_variation.py`: deterministic row shuffle, harmless column-name variants resolved by ingest aliases, optional-field subset drops) per PRD 13.3 anti-overfitting rules; strict label firewall enforcement throughout.
- Hardening & Edge-Case Battery (`backend/tests/hardening/test_hardening_battery.py`):
  - Empty database and empty input files: zero crashes, zero variance, clean exit.
  - Scale volume performance: 1,880-row batch reconciles in <0.2s (>10,000 rec/s throughput).
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

- Backend tests: 323 passed / 0 failed / 0 skipped.
- Frontend tests: Vitest (2 passed), ESLint (0 errors/warnings), TypeScript (strict zero errors), Next.js production build (100% successful static export).
- Holdout Dataset Scale: 1,880 eligible records (exceeding $\ge 500$ benchmark target and 50 track minimum).
- Match Precision: 100.0% (1,124 / 1,124 explicit matches).
- Record Match Rate: 99.15% (1,864 / 1,880 records).
- Case Classification Accuracy: 100.0% (23 / 23 cases).
- False Verifier Passes: 0 (0 / 23).
- Money-Weighted Dry-Run Error: ₹0.00 (0 paise).
- Proof Package Completeness: 18 / 18 (100%).
- Ambiguous Case Escalation: 5 / 5 (100% preserved as UNRESOLVED).
- Throughput: 9,859.51 rec/s (final holdout agent-mode benchmark run).
- Replay Idempotency Hash: `69c89e71cbf7b7cc39934069f0962bb3837d2a7c3fae7c275a8eedd7b476f31e`.
- Duplicate Ledger Adjustments: 0 (measured by replay diagnostics across independent replay databases; recorded in `artifacts/benchmark/final.json` → `replay_diagnostics`).

## Known Limitations

- Live external LLM provider API keys remain optional and separate from offline deterministic fake provider benchmark tests.
- Razorpay Test Mode client is read-only and synthetic offline-first; no live real money movements or production ERP mutation capabilities exist.

## Next Exact Step

Phase 8 - Submission Release: implementation-accurate README, exact setup commands, architecture/data/threat docs, screenshots from actual benchmark values, five-minute demo script, fresh-clone verification, and the submission tag. Optional work (e.g., ARGUS Voice Control Layer) is now permitted only after the Phase 7 gate - which has passed - and must follow PRD §18 order and its own acceptance gate.
