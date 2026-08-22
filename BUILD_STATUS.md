# ARGUS Build Status

Current phase: 1 — Synthetic Data, Ground Truth, and Isolation
Status: PASSED
Last verified commit: gate artifact produced by an independent run on the Phase 1 working tree at HEAD `037979b9beb6cf41654e844cb50ba2a77a99abd7` (pre-commit); committed as `feat(data): add deterministic isolated finance benchmark`
Last evaluation artifact: artifacts/evaluation/phase-01.json

## Implemented

- Evaluator-only package `backend/app/evaluation` (label firewall enforces that runtime `backend/app` api/domain/persistence/main/config never import it): frozen `GenerationSpec` profiles; deterministic clean-corpus generator (payments → refunds → settlements → bank → ledger, integer paise only, single seeded RNG, no wall-clock); the four frozen exception injectors as separate functions with disjoint evidence sets; adversarial corpus covering all 9 PRD adversarial cases plus out-of-order, missing-row, and timing-boundary phenomena; deterministic CSV/JSON writer with sha256 file hashes and reproducibility hash; independent parse-back checks (`control_totals.py`) that re-derive conservation, referential integrity, candidate rules, clean structure, and the variance equation from written files plus labels alone.
- Review correction 1 — root `manifest.json` is strictly input-only raw facts (dataset_version, profile, seed, columns, raw row counts, file sha256, reproducibility hash); eligible/quarantine/duplicate counts and normalized totals live in evaluator-only `labels/manifest.json` and `phase-01.json`. Post-injection variance proven per case and by source reference (`observed_ledger_sum(ref) + delta == expected_clean_sum(ref)`) and in aggregate, so duplicate and missing-refund anomalies cannot cancel.
- Review correction 2 — separated candidate-count assertions: normal and UTR-less-but-unique settlements exactly 1 candidate; missing-bank case exactly 0; twin ambiguity exactly 2; partial-refund aggregate at least 2 refund composition candidates (fixture r3 = r1 + r2 gives exactly 2). Referential integrity: refunds→payments, payment settlement refs, ledger source_reference by source_type, 1:1 UTR pairing with the labelled missing-bank exception, and identifier uniqueness where the only repeat is a labelled `DUPLICATE_DELIVERY` row.
- Review correction 3 — `labels/manifest.json` carries `labels_sha256` (sha256 of labels.json bytes), label schema version, case/row-expectation counts; byte-identical regeneration of labels.json and its manifest is tested.
- Datasets: `datasets/dev` (seed 4104), `datasets/adversarial` (seed 4105), `datasets/holdout/spec.json` (seed 9107, spec-only; generation guarded by `--unfreeze-holdout` until the Phase 7 freeze). `scripts/generate_dataset.py` and `scripts/check_label_isolation.py` CLIs. `docs/data_dictionary.md` documents every field, sign convention, equation, and rule.
- `scripts/verify_phase.py`: `SUPPORTED_PHASES={0,1}`, per-phase names/dispatch; Phase 1 = complete unchanged Phase 0 step list + 7 blocking dataset steps (temp-root generation + byte-compare vs committed for both profiles, label-isolation check, targeted dataset pytest, evaluator-side gate assertions). 58 new unit tests across three files plus `test_verify_phase_safety.py` dispatch tests.

## Commands and Results

Recorded in `artifacts/evaluation/phase-01.json` (independent run: status PASS, 22 steps, 0 known failures, started 2026-08-22T12:54:28Z, finished 2026-08-22T12:55:01Z, HEAD `037979b9be...`):

- `.venv\Scripts\python scripts\verify_phase.py --phase 1` — **PASS** (RUNNING-first lifecycle; exit code 0)
- Complete Phase 0 step list unchanged and green: preflights, ruff check/format (29 files), mypy strict (18 files), backend pytest **123 passed / 0 failed / 0 skipped** (3.93s), frontend lint/typecheck/vitest/build, both boot probes, secret scan, gitignore coverage; optional Playwright e2e **PASS, 1 test** (4.66s, non-blocking as designed)
- `dataset-generate-dev` / `dataset-generate-adversarial` (unique tmp roots) — PASS (12 ms / 8 ms)
- `dataset-reproducibility-dev` / `dataset-reproducibility-adversarial` — PASS (byte-identical regeneration of inputs, labels, and manifests)
- `check-label-isolation` — PASS (label firewall holds)
- `dataset-tests` (targeted) — PASS (**55 passed** in 0.42s)
- `dataset-gate-assertions` — PASS (dev ≥ 100 eligible; all 4 categories; candidate rules; variance equation per-case/aggregate; referential integrity; clean-structure; manifest hashes; holdout seed separated)

## Actual Metrics

- Dev (seed 4104): **282 eligible rows** — payments 96, refunds 18, settlements 18, bank 18, ledger 132; **12 cases** (3 × each of DUPLICATE_LEDGER_POSTING, MISSING_REFUND_POSTING, SETTLEMENT_TIMING_WINDOW_SHIFT, AMBIGUOUS_EVIDENCE); 0 quarantine, 0 duplicate deliveries; reproducibility hash `85adb7d0b919145616b672a0a267fabd405d88dccc44f774f619ac0555cfea67`
- Adversarial (seed 4105): 67 raw rows (payments 20, refunds 7, settlements 6, bank 5, ledger 29) → **64 eligible**, 2 quarantine-expected (USD currency, invalid date), 1 labelled duplicate delivery, 3 AMBIGUOUS_EVIDENCE cases (partial-refund aggregate, twin settlements, missing bank row) + 14 row expectations; reproducibility hash `d5e176ddeb20ff9f508f07667656376ecb22a7f6807932f1523f3b3e85e5d62a`
- Clean-corpus conservation holds exactly (settlement equation per row and corpus-wide; bank, ledger-1100, ledger-2100 identities); post-injection variance equation holds per case, per reference, and in aggregate
- Dev totals (paise, eligible): bank credit 186,087,906; ledger 1100 = 186,087,906; ledger 2100 observed 194,451,301 + deltas −8,363,395 = clean 186,087,906
- Generator runtime: dev 12 ms, adversarial 8 ms (independent run); scale smoke (test-only benchmark profile, ~590 rows) generates and passes all checks well under budget, not committed
- Backend unit tests: **123 passed** (65 Phase 0 + 58 Phase 1); targeted dataset suite: 55 passed

## Known Limitations

- Byte-identical determinism is guaranteed on the pinned Python 3.12 line (seeded `random.Random` sequence stability is a CPython behaviour, not a language guarantee); the gate's byte-compare makes any drift loudly visible
- Committed datasets are enforced against generator drift by the gate; regenerating with a different seed produces different identities by design
- Per-row content hashes and normalization-time provenance are Phase 2 work (PRD 8.1); Phase 1 provides stable IDs plus file-level sha256
- Holdout is spec-only (`datasets/holdout/spec.json`, seed 9107); inputs/labels are generated at the Phase 7 freeze via `--unfreeze-holdout`, with the planned ordering/optional-field/date-format/column-name variation applied then
- The 500+ submission benchmark dataset itself is Phase 7 work; Phase 1 proved scale via the test-only benchmark profile (~590 rows)
- Optional Playwright e2e remains non-blocking for the gate (PASS, 1 test, on the independent run)

## Next Exact Step

Phase 2 — Normalization, Reconciliation, and Evidence Graph (PRD §16): typed source adapters over `datasets/*/inputs`, money/status/date normalization with quarantine (never dropping rows), the matching hierarchy with consumption rules, independent control totals, typed cases for residual inconsistencies, graph serialization, idempotent rules-only run path, and `scripts/verify_phase.py --phase 2` (to be extended; Phase 0 and 1 step lists unchanged).
