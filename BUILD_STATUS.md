# ARGUS Build Status

Current phase: 2 — Normalization, Deterministic Reconciliation, and Evidence Graph
Status: PASSED
Last verified commit: gate artifact produced by an independent run on the Phase 2 working tree at HEAD `9bcfc3155e0cea40a5b18045e26690adf93856fe` (pre-commit); suggested commit `feat(recon): add deterministic reconciliation and evidence graph`
Last evaluation artifact: artifacts/evaluation/phase-02.json

## Implemented

- Typed source adapters (`backend/app/importers/adapters.py`) for payments, refunds, settlements, bank entries, and ledger entries: strict header contracts, fixed validation precedence (shape → id → currency → status → money → timestamps → dates), and per-row content hashes over canonical JSON of ordered `[column, raw_value]` pairs (reorder-stable).
- Ingest (`importers/ingest.py`): rows grouped by (source type, source record id) BEFORE canonicalization; identical hashes accept one economic record plus `DUPLICATE_DELIVERY` markers (canonical pointer = min row number, excluded from the economic hash); differing hashes quarantine EVERY conflicting row as `DUPLICATE_ID_CONFLICT` — never first-wins. Identity accepted + quarantined + duplicates == raw rows, asserted per file and end-to-end.
- Normalized records with provenance (`domain/records.py`), integer paise everywhere, event/settlement/accounting times distinct; quarantined rows stored with reason + detail + raw payload, counted and traceable, never dropped.
- Persistence v2: transactional migration chain (`persistence/migrations.py`) — fresh DBs build the v1 baseline then migrate, so migration DDL runs on every boot; a failed migration rolls back fully and raises typed `PersistenceMigrationError` BEFORE any runs table exists (no run row is claimed FAILED); only failures after run creation persist status FAILED. 11 new tables (runs, source_rows, 5 normalized record tables, match_groups/match_members with signed contributions, cases with variance/affected/proposed-delta-NULL, case_evidence). Bulk persistence wrapped in one transaction (95× throughput gain).
- Matching engine (`reconciliation/engine.py`): strict hierarchy — exact FKs (refund→payment, settlement membership aggregation with the settlement as zero-contribution TARGET member, ledger→source with amount semantics), exact UTR + compatible amount, UTR-less unique amount-in-±24h-window (unique both directions, UTR-bearing settlements never fall back), unique refund composition (subset-sum ≤ 16). Consumption by typed slot (SETTLEMENT_MEMBERSHIP, REFUND_PARENT, BANK_CREDIT_MATCH, LEDGER_SOURCE_MATCH, REFUND_COMPOSITION): a payment legitimately holds membership + refund-parent + ledger-source simultaneously; bank credits and ledger rows exclusive; duplicate ledger rows consume the lexicographically smaller id (row-number independent).
- Case detectors (`reconciliation/detectors.py`): the four frozen categories with anchor-exact evidence; separate `variance_paise` (observed − expected, documented sign convention), `affected_amount_paise`, and `proposed_delta_paise` = NULL in Phase 2; `variance_scope` LEDGER/BANK/OTHER so the evaluator can independently verify the ledger-side residual; AMBIGUOUS cases carry honest residuals (missing-bank −net; twins/composition aggregate 0 with non-zero affected); generic residual sweep guarantees every accepted record ends matched, case-attached, or quarantined.
- Control totals (`reconciliation/totals.py`) reproducible from stored records; `verify_match_invariants` enforces aggregation Σ == amount and zero-sum transfer semantics.
- Evidence graph (`graph/evidence.py`): derived from records/matches/case evidence (never stored separately); 5 record node types + CASE nodes; edges carry rule id/version and EXACT/RULE confidence; serialization fails loudly on unknown ids.
- Rules-only run path (`runs.py` + `scripts/run_benchmark.py`): runtime receives ONLY a dataset `inputs` directory; two fresh-database runs must produce equal economic-output hashes (canonical JSON over matches/cases/quarantine/duplicates/totals/counts, excluding row numbers, ordering, timestamps, surrogates); same idempotency key returns the stored run; `force` is failure-safe — the replacement is fully recomputed first and swapped inside ONE transaction (delete + insert), so a failed forced recomputation rolls back to the previous completed result; post-creation failures (any `Exception`: reconciliation, graph, persistence, sqlite) persist status FAILED, while `KeyboardInterrupt`/`SystemExit` (`BaseException`) are never swallowed and migration failures happen before any runs table exists.
- Evaluator benchmark (`evaluation/benchmark.py`): labels loaded only AFTER the runtime output is finalized; frozen denominators with every numerator AND denominator reported explicitly; per-relationship independent verification; one-to-one case matching on category + exact anchor evidence (extra evidence does not count); scope-aware residual comparison.
- Label firewall strengthened: `RUNTIME_PY_ROOTS` now also scans `importers/`, `reconciliation/`, `graph/`, `runs.py`; plus a subprocess-isolated audit-hook test proving the runtime never opens any path under a `labels/` directory (positive run + canary negative control; no audit hook in the main pytest process).
- Domain contract: added `QuarantineReason`, `RelationshipType` (incl. CASE_EVIDENCE), `NodeType.CASE`; TS mirror updated; `contracts/domain_enums.json` regenerated (16 enums).
- `scripts/verify_phase.py`: `SUPPORTED_PHASES={0,1,2}`; Phase 2 = complete unchanged Phase 0 + Phase 1 lists + 9 appended blocking steps with explicit test file paths (no wildcards): migration, normalization, reconciliation (5 files), benchmark-evaluator, integration, adversarial pytest steps, dev + adversarial benchmarks, and evaluator-side `phase2-gate-assertions`.

## Commands and Results

Recorded in `artifacts/evaluation/phase-02.json` (independent run: status PASS, 31 steps, 0 known failures, next_phase 3):

- `.venv\Scripts\python scripts\verify_phase.py --phase 2` — **PASS** (RUNNING-first lifecycle; exit 0)
- Complete Phase 0 step list unchanged and green (ruff check/format 57 files, mypy strict 33 files, backend pytest **198 passed**, frontend lint/typecheck/vitest/build, both boot probes, secret scan, gitignore coverage)
- Complete Phase 1 step list unchanged and green (dataset regeneration byte-identical for both profiles, label isolation, 55 dataset tests, dataset gate assertions)
- `unit-tests-migration` — PASS (**6 passed**): fresh v2, v1→v2 upgrade preserving metadata, injected failure leaves schema_version 1 with zero v2 tables and typed `PersistenceMigrationError`, retry succeeds
- `unit-tests-normalization` — PASS (**16 passed**); `unit-tests-reconciliation` — PASS (**43 passed** across matching/cases/totals/graph/idempotency incl. FAILED-persistence and failure-safe-force regression tests); `unit-tests-benchmark-evaluator` — PASS (**10 passed**)
- `integration-rules-only-run` — PASS (**6 passed**, incl. subprocess labels-access audit guard); `adversarial-tests` — PASS (**14 passed**)
- `benchmark-rules-only-dev` / `benchmark-rules-only-adversarial` — PASS (reports at `artifacts/benchmark/phase-02-{dev,adversarial}.json` with explicit numerators/denominators)
- `phase2-gate-assertions` — PASS (precision 1.0; 282 eligible; 12 cases 3×4 one-to-one on anchors; idempotent rerun; graph valid; adversarial 64/2/1/3)

## Actual Metrics

- Dev (rules-only): **match precision 177/177 = 1.0** (0 false relationships); record match rate **273/282 = 0.968085**; case classification **12/12 = 1.0** (no false positives, no misses); eligible canonical 282, quarantined 0, duplicate deliveries 0; throughput **5879.59 records/s**; economic output hash `e120be770bca11ff75dc1c4c85df438de90473c024d5e974414d68d32b8b8053` with byte-identical rerun; graph 294 nodes / 297 edges (12 CASE nodes, 36 CASE_EVIDENCE edges); residual variance 8,363,395 paise ledger-scoped (matches `|observed − clean_reference|` exactly), 0 bank-scoped; control totals equal the labels manifest (payment net 211,903,406; settlement net 186,087,906)
- Adversarial (rules-only): **match precision 42/42 = 1.0**; record match rate **60/64 = 0.9375**; cases **3/3 = 1.0**; eligible 64, quarantined 2 (UNSUPPORTED_CURRENCY, INVALID_TIMESTAMP), duplicate deliveries 1; throughput 1792.55 records/s; economic hash `06218d4f30b181f08b56a99df7c3d0359dcb9ebe9cfa7456cdda4ce296b593bd` with byte-identical rerun; graph 67 nodes / 69 edges; residual 0 ledger-scoped / 4,421,042 paise bank-scoped (missing-bank evidence)
- Reorder invariance: dev seeds 1–5 and adversarial seeds 11–12 all preserve the economic hash; reruns in fresh databases byte-identical
- Backend tests: **198 unit passed** (65 Phase 0 + 58 Phase 1 + 75 Phase 2) + 20 integration/adversarial; migration tests 6/6; run-failure semantics covered by 4 regression tests (unexpected post-creation failure persists FAILED; computation and in-transaction swap failures on `force` retain the previous completed run)

## Known Limitations

- Bank-side residual variance (missing bank evidence) is runtime-computed; Phase 2 cross-checks it through case-anchor matching, not an independent evaluator derivation (documented in the benchmark report note)
- Extra unknown CSV columns quarantine their rows rather than being tolerated; broader schema-drift tolerance is Phase 7 hardening
- Settlement conservation violations (Σ contributions ≠ net) suppress the membership match and become CONTROL_TOTAL_VIOLATION cases; no fixture exercises this defensive path
- Match/case ids are content-derived hashes — stable but not human-readable by design
- The 500+ record submission benchmark and holdout generation remain Phase 7 work

## Next Exact Step

Phase 3 — Verifier, Proof Packages, and Dry-Run Core (PRD §16): verifier interface with stable reason codes, duplicate-ledger / missing-refund / timing-window / ambiguity verification, equations and supported/conflicting evidence ids, canonical proof packages, dry-run calculation without persistence mutation, and authority classification; `scripts/verify_phase.py --phase 3` appended with Phase 0–2 step lists unchanged.
