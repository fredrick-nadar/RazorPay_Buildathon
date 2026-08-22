# ARGUS Build Status

Current phase: 3 - Verifier, Proof Packages, and Dry-Run Core
Status: PASSED
Last verified commit: gate artifact produced by an independent run on the Phase 3 working tree at HEAD `30a02e1a3f4649611ef0cb9547f2b0b41993499d` (pre-commit); suggested commit `feat(verify): add falsifiable proof packages and ledger dry-run`
Last evaluation artifact: artifacts/evaluation/phase-03.json

## Implemented

- Phase 3 verifier domain (`backend/app/verifier/`): rule manifest `verify-rules-v1`, stable verifier rule ids, system-generated structured hypotheses, evidence snapshots, category verifiers, global pre-checks, canonical proof packages, proof staleness checks, and deterministic verification orchestration.
- Phase 3 correction domain (`backend/app/corrections/`): synthetic merchant authority policy `authority-policy-v1` and pure DRAFT dry-run previews. Dry-run previews may be persisted as run outputs only; they never mutate imported rows, normalized rows, ledger truth, or financial postings.
- Category behavior: duplicate-ledger and missing-refund PASS produce nonzero deltas and `APPROVAL_REQUIRED`; timing-window PASS produces zero delta and `VERIFIED_RESOLVED`; ambiguous evidence structurally cannot PASS and remains `UNRESOLVED`.
- Proof packages: each case gets cited evidence ids, supported/conflicting evidence, concrete equations, verifier rule id/version, reconciliation/verifier manifest fingerprints, canonical SHA-256 hash, authority decision, and optional DRAFT dry-run preview for PASS proofs.
- Persistence v3: migration chain now walks v1 -> v2 -> v3 and adds `hypotheses`, `proofs`, and `corrections` tables. `corrections` rows are stored previews only, not applied corrections. No `SIMULATED_CORRECTION` ledger rows are created in Phase 3.
- Run integration: `execute_run` now verifies cases after deterministic reconciliation, persists hypotheses/proofs/DRAFT previews in the same run output flow, includes final case statuses/deltas in the economic hash, and uses `run-v2` idempotency with the verifier manifest fingerprint.
- Label firewall: runtime roots now include `backend/app/verifier` and `backend/app/corrections`; runtime still cannot import evaluator code or read `datasets/**/labels`.
- Evaluator metrics: benchmark reports now include outcome agreement, delta agreement, ambiguous escalation, false verifier pass count, proof completeness, and money-weighted dry-run error. `run_benchmark.py` fails on false passes, missed escalation, outcome/delta mismatch, incomplete PASS proofs, or nonzero dry-run error.
- Scope safety: `backend/tests/unit/test_scope_safety.py` mechanically blocks Phase 4 creep by rejecting approve/apply/update-ledger/mark-resolved style callables, persistence imports from verifier/corrections, and real `LedgerEntryRecord` construction with `SIMULATED_CORRECTION`.
- Documentation: `docs/verification_rules.md` records the verifier rules, proof schema behavior, dry-run boundary, and authority mapping.
- `scripts/verify_phase.py`: `SUPPORTED_PHASES={0,1,2,3}`. Phase 3 runs the complete Phase 0, Phase 1, and Phase 2 gates first, then appends explicit Phase 3 steps: scope safety, verifier tests, migration v3 tests, benchmark evaluator v3 tests, dry-run integration, dev/adversarial Phase 3 benchmarks, and evaluator-side Phase 3 assertions.

## Commands and Results

Recorded in `artifacts/evaluation/phase-03.json` (independent run: status PASS, 39 steps, 0 known failures, started `2026-08-22T20:04:31+00:00`, finished `2026-08-22T20:05:31+00:00`, next_phase 4):

- `.venv\Scripts\python scripts\verify_phase.py --phase 3` - PASS (RUNNING-first lifecycle; exit 0)
- Complete Phase 0 step list green: Ruff check, Ruff format-check, mypy strict, backend pytest, frontend lint/typecheck/Vitest/build, backend and frontend boot probes, secret scan, gitignore coverage.
- Complete Phase 1 step list green: deterministic dataset regeneration byte-identical for dev and adversarial, label isolation, 55 dataset tests, dataset gate assertions.
- Complete Phase 2 regression step list green: migration, normalization, reconciliation, benchmark evaluator, integration, adversarial tests, dev/adversarial benchmark reruns, Phase 2 assertions.
- Phase 3 steps green: `scope-safety` 3 passed; `unit-tests-verifier` 6 passed; `unit-tests-migration-v3` 8 passed; `unit-tests-benchmark-evaluator-v3` 10 passed; `integration-dry-run` 1 passed; `benchmark-rules-only-phase3-dev` PASS; `benchmark-rules-only-phase3-adversarial` PASS; `phase3-gate-assertions` PASS.

## Actual Metrics

- Backend unit tests: 209 passed / 0 failed / 0 skipped, 1 warning.
- Dev benchmark: match precision 177/177 = 1.0; record match rate 273/282 = 0.968085; case classification 12/12 = 1.0; Phase 3 throughput 4995.41 records/s; economic output hash `06a13536e94b64df905e8f617700038f9221a78f160c612b96a96e40702d8629`.
- Dev verification: outcome agreement 12/12; delta agreement 12/12; false verifier passes 0; ambiguous escalation 3/3; PASS proof completeness 9/9; DRAFT dry-run count 9; dry-run absolute variance after 0 paise; final statuses: 6 `APPROVAL_REQUIRED`, 3 `VERIFIED_RESOLVED`, 3 `UNRESOLVED`.
- Adversarial benchmark: match precision 42/42 = 1.0; record match rate 60/64 = 0.9375; case classification 3/3 = 1.0; Phase 3 throughput 1489.94 records/s; economic output hash `9787355f41947e9c6f77ed144ca044effa12cc5a1a19058d8fb500c50c590e78`.
- Adversarial verification: outcome agreement 3/3; delta agreement 3/3; false verifier passes 0; ambiguous escalation 3/3; verifier statuses: 0 PASS, 3 INCONCLUSIVE, 0 FAIL; final statuses: 3 `UNRESOLVED`; DRAFT dry-run count 0.

## Known Limitations

- Phase 3 still has no AI investigator. Hypotheses are system-generated from deterministic cases so the verifier/proof/dry-run core can be falsified before model investigation is added.
- Phase 3 stores DRAFT correction previews but has no approval, apply, simulated-apply, dashboard, voice, multilingual UI, or Phase 4+ behavior.
- Dry-run proposed ledger entries are preview value objects only. No persisted ledger entry with `SIMULATED_CORRECTION` exists.
- The 500+ record submission benchmark and holdout generation remain later work.

## Next Exact Step

Phase 4 - Bounded AI Investigator (PRD section 16): add the model-facing investigation layer only for unresolved residuals, route every proposed explanation through the deterministic Phase 3 verifier, keep ambiguity unresolved, keep voice/multilingual/dashboard/application out of scope unless the governing phase permits them.
