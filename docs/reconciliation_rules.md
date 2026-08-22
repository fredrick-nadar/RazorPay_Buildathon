# ARGUS CONTROL — Reconciliation Rules (Phase 2)

Deterministic normalization, matching, case, and evidence-graph contracts
implemented in Phase 2. Everything here is rules-only: no model dependency
exists anywhere on the run path, and no runtime code can reach ground-truth
label data (mechanically enforced by the extended label firewall and by the
subprocess audit-hook test in `backend/tests/integration/test_rules_only_run.py`).

## 1. Normalization and provenance

- Money: exact decimal-rupee strings parsed to **signed integer paise**
  (`app.domain.money.paise_from_decimal_rupees`); floats are impossible by
  construction. Currency must be `INR`.
- Timestamps are strict `YYYY-MM-DDTHH:MM:SSZ` UTC; `accounting_date` and
  `value_date` are strict `YYYY-MM-DD`. Event time, settlement time, and
  accounting date remain distinct fields.
- Per-row **content hash**: SHA-256 over canonical JSON of ordered
  `[column, raw_value]` pairs — identical for byte-identical rows and stable
  under file reordering.
- **Provenance** on every normalized record: source file, 1-based row
  number, source record id, content hash.
- Quarantine reasons (`QuarantineReason`): `UNSUPPORTED_CURRENCY`,
  `INVALID_TIMESTAMP`, `INVALID_DATE`, `INVALID_MONEY`,
  `MISSING_REQUIRED_FIELD`, `UNKNOWN_STATUS`, `INVALID_ROW_SHAPE`,
  `DUPLICATE_ID_CONFLICT`. Quarantined rows are stored, counted, and
  traceable — never dropped.
- Duplicate-id resolution is grouped by `(source type, source record id)`
  **before** canonicalization: identical content hashes accept exactly one
  economic record (the rest become `DUPLICATE_DELIVERY` pointing at the
  canonical row); differing content hashes quarantine **every** conflicting
  row as `DUPLICATE_ID_CONFLICT` — never "first row wins".
- Row identity: accepted + quarantined + duplicate-delivery rows always
  equal the raw row count per file.

## 2. Matching hierarchy (strongest first)

| Tier | Rule id | Evidence |
|---|---|---|
| 1 | `R-EXACT-REFUND-PAYMENT` | `refunds.payment_id` |
| 1 | `R-EXACT-PAYMENT-SETTLEMENT` | `payments.settlement_id` (membership aggregation) |
| 1 | `R-EXACT-LEDGER-SOURCE` | `ledger.source_reference + source_type` + amount semantics (PAYMENT `+net`/2100, REFUND `−refund`/2100, SETTLEMENT `+net`/1100) |
| 2 | `R-UTR-AMOUNT-BANK` | exact UTR **and** compatible amount |
| 6 | `R-UNIQUE-AMOUNT-WINDOW-BANK` | UTR absent on both sides; exactly one credit with equal amount within `[window_start − 24h, window_end + 24h]`, unique in both directions |
| — | `R-UNIQUE-REFUND-COMPOSITION` | aggregate deduction rows resolve against exactly one refund subset (≤16 refunds) |

Amounts are never sufficient alone: every amount-based rule also requires a
strong identifier or a uniqueness proof. A UTR-bearing settlement never
falls back to amount matching. Ties (≥2 candidates) and empty candidate sets
become explicit `AMBIGUOUS_EVIDENCE` cases — the engine never guesses.

## 3. Consumption slots

Consumption is keyed by typed slot, not by record: `SETTLEMENT_MEMBERSHIP`
(each payment/refund once, many members per settlement), `REFUND_PARENT`
(each refund once; the payment is never consumed), `BANK_CREDIT_MATCH`
(exclusive 1:1 on both sides), `LEDGER_SOURCE_MATCH` (each ledger row once;
one direct posting per source per kind), `REFUND_COMPOSITION` (consumes the
composed refunds' posting expectation). A payment therefore legitimately
holds membership + refund-parent + ledger-source relationships
simultaneously; bank credits and individual ledger entries are exclusive.

## 4. Match money semantics

- **Aggregation groups** (`MEMBER_OF_SETTLEMENT`): Σ signed contributions ==
  stored `amount_paise` == settlement net (payments `+net`, refunds
  `−refund`, settlement itself a zero-contribution `TARGET` member).
- **Zero-sum groups** (`REFUND_OF_PAYMENT`, `SETTLEMENT_BANK_CREDIT`,
  `LEDGER_SOURCE`, compositions): Σ contributions == 0 with the transfer
  magnitude stored as the amount; compositions sum their positive components
  to the amount against the negative booking row.
- `verify_match_invariants` mechanically enforces both branches on every
  stored group.

## 5. Cases: variance vs affected amount vs proposed delta

Every case stores three separate fields:

- `variance_paise` — observed minus expected over the case's reference
  scope, under a fixed documented sign convention:
  - duplicate ledger posting: `+posted amount` (ledger overstatement);
  - missing refund posting: `+refund amount` (expected deduction absent);
  - timing-window shift: `0` (period attribution only);
  - missing bank evidence: `−settlement net` (non-zero residual);
  - twin/composition ambiguity: aggregate `0` with a non-zero affected
    amount.
- `affected_amount_paise` — money involved in the uncertainty.
- `proposed_delta_paise` — **always NULL in Phase 2**; only a Phase 3
  verifier `PASS` may derive a correction delta. Runtime code never reads
  label deltas.

`variance_scope` tags each residual as `LEDGER`, `BANK`, or `OTHER`, so the
evaluator can compare the ledger-scoped runtime residual against the
independently derived `|observed ledger − clean reference|` gap.

Case evidence sets are exactly the anchors of each phenomenon (dev:
`[pay, led1, led2]`, `[rfd, pay, stl]`, `[stl, led]`, `[stl1, stl2, bnk1,
bnk2]`), which the benchmark enforces one-to-one with labels — extra
unrelated evidence does not count as correct.

## 6. Idempotency and the economic output hash

- Run idempotency key: SHA-256 over `run-v1 | tenant | inputs fingerprint |
  normalizer version | rule manifest version`. Re-running returns the stored
  completed run; `force=True` recomputes in place.
- Economic output hash: SHA-256 over canonical JSON of rule manifest,
  counts, control totals, sorted matches (with signed contributions), sorted
  cases, quarantine, and duplicate deliveries — excluding row numbers,
  ordering, timestamps, run ids, and surrogate keys. Row reorderings and
  reruns are byte-verifiable.

## 7. Benchmark denominators (frozen)

- record-level match rate = correctly dispositioned matched eligible
  records / eligible canonical records (duplicate deliveries and
  quarantined rows reported separately, excluded from the denominator);
- match precision = independently verified deterministic relationships /
  all predicted relationships;
- case classification accuracy = one-to-one anchor-matched cases / labelled
  cases.

Every numerator and denominator is reported explicitly in
`artifacts/benchmark/*.json`; the evaluator loads labels only after the
runtime output is finalized, and the runtime only ever receives a dataset
`inputs` directory.
