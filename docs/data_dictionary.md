# ARGUS CONTROL — Data Dictionary (Phase 1)

Deterministic synthetic datasets for the ARGUS evaluation benchmark. All data
is fictional (`ARGUS DEMO MERCH`, account fingerprint `FP-ARGUS-DEMO-01`).
No real customer, merchant, bank, card, UPI, or production data is used.
Fee (2%) and tax (18% of fee) are synthetic merchant policy values, not
Razorpay policy.

## 1. Layout

```text
datasets/
  dev/            seed 4104, 282 eligible rows, 12 labelled cases
    inputs/       payments.csv refunds.csv settlements.csv bank_entries.csv ledger_entries.csv
    labels/       labels.json (ground truth) + manifest.json (evaluator-only metadata)
    manifest.json raw input facts only
  adversarial/    seed 4105, ~64 eligible rows, 3 cases + row expectations
    (same layout)
  holdout/
    spec.json     specification only; data generated at the Phase 7 freeze
```

Runtime code may read `inputs/` only. `labels/` is evaluator-only and is
mechanically unreachable from runtime code (`scripts/check_label_isolation.py`).

## 2. Conventions

- **Money**: CSV carries exact 2-decimal rupee strings (`"1234.56"`, `"-0.01"`).
  All computation uses signed integer paise; decimal strings are parsed with
  exact string arithmetic (`app.domain.money.paise_from_decimal_rupees`).
  Binary floating point is never used.
- **Timestamps**: ISO-8601 UTC, `YYYY-MM-DDTHH:MM:SSZ`. `accounting_date` and
  `value_date` are dates (`YYYY-MM-DD`). Source event time, settlement time,
  and accounting date remain distinct fields.
- **Identifiers**: `<prefix>_<10-char base62>` drawn from the seeded RNG
  (`pay_`, `rfd_`, `stl`, `bnk_`, `led_`, `order_`); UTRs are `UTIR` + 12
  digits. IDs are unique per domain; the only tolerated repeat is an exact
  duplicate-delivery row labelled `DUPLICATE_DELIVERY`.
- **Determinism**: one `random.Random(seed)`, sequential draws, sorted
  iteration, no wall-clock/environment/locale input. Same seed + dataset
  version + code ⇒ byte-identical files and hashes. Guaranteed on the pinned
  Python 3.12 line; the reproducibility hash makes any drift visible.
- **Per-row content hashes** are a Phase 2 normalization duty (PRD 8.1);
  Phase 1 provides stable IDs plus file-level sha256 in the root manifest.

## 3. Input schemas

### payments.csv
| Column | Meaning |
|---|---|
| payment_id | Unique payment identifier |
| order_id | Unique merchant order reference |
| status | `CAPTURED` |
| currency | `INR` (adversarial: one labelled USD quarantine row) |
| gross_amount | Gross charge (rupees) |
| fee_amount | Synthetic 2% fee, `(gross*2+50)//100` paise |
| tax_amount | 18% of fee, `(fee*18+50)//100` paise |
| captured_at_utc | Capture time (adversarial: exact window-boundary times) |
| settlement_id | Settlement that aggregated this payment ("" when unsettled/quarantined) |

### refunds.csv
| Column | Meaning |
|---|---|
| refund_id | Unique refund identifier |
| payment_id | Parent payment (must exist) |
| status | `PROCESSED` |
| currency | `INR` |
| refund_amount | Refund value; Σ refunds per payment never exceeds gross |
| created_at_utc | Refund creation (adversarial: one labelled invalid-date quarantine row) |
| settlement_id | Settlement whose adjustment carries the refund |

### settlements.csv
| Column | Meaning |
|---|---|
| settlement_id | Unique settlement identifier |
| settled_at_utc | Settlement execution time |
| window_start_utc / window_end_utc | 24h aggregation window `[start, end)` |
| status | `PROCESSED` |
| currency | `INR` |
| gross_credit | Σ member payment gross |
| fee_amount / tax_amount | Σ member fee / tax |
| adjustment_amount | Signed; `-(Σ allocated refunds)` |
| net_amount | `gross_credit − fee − tax + adjustment` (conserves exactly) |
| utr | Bank reference; optional ("" for UTR-less fixtures) |

### bank_entries.csv
| Column | Meaning |
|---|---|
| bank_entry_id | Unique credit identifier |
| posted_at_utc | Posting time (may legitimately trail the window) |
| value_date | Value date of the posting |
| currency | `INR` |
| signed_amount | Credit amount = settlement net (positive) |
| narration | Untrusted free text (one adversarial row carries a prompt-injection sentence) |
| utr | Optional; when present it pairs 1:1 with exactly one settlement |
| account_fingerprint | Fictional `FP-ARGUS-DEMO-01`; no real account numbers |

### ledger_entries.csv
| Column | Meaning |
|---|---|
| ledger_entry_id | Unique posting identifier |
| account_code | `1100-BANK-OPERATING` or `2100-PAYMENTS-CLEARING` |
| accounting_date | Booking date (single-signed merchant view, PRD 6.6) |
| currency | `INR` |
| signed_amount | See sign conventions below |
| source_reference / source_type | Provenance: `pay_*`/`PAYMENT`, `rfd_*`/`REFUND`, `stl_*`/`SETTLEMENT` |
| description | Free text |
| entry_origin | `IMPORTED` in generated data |

**Ledger sign conventions** (one row per source event):
payment captured → `2100` `+net` (net = gross − fee − tax); refund processed →
`2100` `−refund`; settlement credited → `1100` `+settlement.net`.

## 4. Clean conservation identities

On the eligible, deduplicated corpus (and exactly on the pre-injection
ledger):

```text
Σ settlement.net == Σ payment.net − Σ refunds
bank credit total == Σ settlement.net − Σ(missing-bank labelled settlements)
Σ ledger(1100)   == Σ settlement.net
Σ ledger(2100)   == Σ payment.net − Σ refunds
```

## 5. Post-injection variance equation (review correction 1)

After exception injection the anomalous ledger is NOT required to equal the
clean ledger. Instead, per case and by source reference:

```text
observed_ledger_sum(reference) + expected_delta_paise == expected_clean_sum(reference)
```

with `expected_clean_sum` derived from the inputs alone (`payment.net` or
`−refund`), and in aggregate:

```text
observed_ledger_total + Σ(non-null expected_delta_paise)
    == clean_reference.ledger_total_paise
    == Σ payment.net − Σ refunds + Σ settlement.net
```

Because each case is proven on its own reference, a duplicate posting
(+net posted twice, delta −net) and a missing refund posting (absent −refund,
delta −refund) can never cancel each other.

## 6. Labels — `labels/labels.json` (evaluator-only)

Header: `dataset_version`, `label_schema_version`, `profile`, `seed`,
`summary{case_count, by_category, row_expectation_count}`.

`cases[]` (PRD 6.13): `case_id`, `expected_category`, `expected_outcome`,
`expected_evidence_ids[]`, `expected_delta_paise|null`, `must_escalate`,
`authoring_notes`.

| Category | Outcome | Delta | Escalate | Construction |
|---|---|---|---|---|
| DUPLICATE_LEDGER_POSTING | APPROVAL_REQUIRED | −payment.net | no | second identical posting, new ledger id only |
| MISSING_REFUND_POSTING | APPROVAL_REQUIRED | −refund | no | refund keeps its input row; ledger row removed |
| SETTLEMENT_TIMING_WINDOW_SHIFT | VERIFIED_RESOLVED | 0 | no | ledger accounting_date moved into the adjacent window; period attribution only |
| AMBIGUOUS_EVIDENCE | UNRESOLVED | null | yes | twin settlements / partial-refund aggregate / missing bank evidence |

`row_expectations[]` (adversarial): per-row ground truth with keys `file`,
`row_number` (1-based data row, `null` for file-scope), `source_record_id`,
`expectation`, `note`. Values: `DUPLICATE_DELIVERY` (+`duplicate_of_row`),
`DISTINCT_EVENTS`, `REORDERED_FILE`, `MATCHABLE_WITHOUT_UTR`,
`QUARANTINE_CURRENCY`, `QUARANTINE_INVALID_DATE`, `INERT_UNTRUSTED_TEXT`,
`OUT_OF_ORDER`, `BOUNDARY_TIME`.

`clean_reference`: `ledger_total_paise`, `ledger_by_account_paise`,
`derivation` — the ledger state immediately before exception injection.

## 7. Manifests

### `manifest.json` — strictly input-only raw facts
`dataset_version`, `profile`, `seed`, `files{relative → rows (raw count),
sha256, columns}`, `reproducibility_hash`
(sha256 of `seed|dataset_version|sorted relative:sha256` pairs). No counts or
totals that require knowing which rows are valid or anomalous; no timestamps.

### `labels/manifest.json` — evaluator-only integrity + anomaly-aware metrics
`label_schema_version`, `dataset_version`, `profile`, `seed`,
`labels_sha256` (sha256 of labels.json bytes), `case_count`,
`row_expectation_count`, plus `eligible_row_count`,
`quarantine_expected_count`, `duplicate_delivery_count`, `totals_paise`
(normalized financial totals over eligible, deduplicated rows).

## 8. Candidate rules (review correction 2)

Documented evaluator-side rules (fixture-construction assertions, not a
matching engine):

- **Bank credit ↔ settlement**: a credit is a candidate for a settlement iff
  `settlement.net == credit.amount` and
  `window_start − 24h ≤ posted_at ≤ window_end + 24h`.
  Normal and UTR-less-but-unique settlements have exactly one candidate
  credit (and their credits exactly one candidate settlement); twin
  settlements/credits have exactly two each; the missing-bank case has
  exactly zero candidate credits.
- **Refund composition**: a ledger refund-deduction row attributed to a
  payment may reverse any subset of that payment's refunds summing to the
  row amount. The partial-refund fixture (r1, r2, r3 = r1 + r2 with two
  aggregate rows of −(r1+r2)) admits exactly two compositions: {r1, r2} and
  {r3}.

## 9. Adversarial matrix

| Phenomenon | Expectation |
|---|---|
| Exact duplicate row delivery | DUPLICATE_DELIVERY — dedup to one economic event |
| Identical amounts, distinct payments | DISTINCT_EVENTS — no false dedup |
| Shuffled payments.csv | REORDERED_FILE — order-independent processing |
| Missing optional UTR | MATCHABLE_WITHOUT_UTR — unique amount+window match |
| USD currency row | QUARANTINE_CURRENCY — quarantined, never dropped |
| Invalid date row | QUARANTINE_INVALID_DATE — quarantined |
| Prompt-injection narration (PRD 10.4 sentence) | INERT_UNTRUSTED_TEXT |
| Two partial refunds + aggregate rows | AMBIGUOUS_EVIDENCE case (2 compositions) |
| Twin settlements, boundary credit | AMBIGUOUS_EVIDENCE case (2 candidates) |
| Settlement without bank credit | AMBIGUOUS_EVIDENCE case (0 candidates) |
| Refund/settlement before parent capture | OUT_OF_ORDER — totals still conserve |
| 00:00:00Z / 23:59:59Z / month-boundary times | BOUNDARY_TIME |

## 10. Seeds and profiles

| Profile | Seed | Base epoch | Purpose |
|---|---|---|---|
| dev | 4104 | 2026-03-02Z | 100+ record development benchmark |
| adversarial | 4105 | 2026-03-30Z | failure-phenomena corpus (spans the month boundary) |
| holdout | 9107 | 2026-06-01Z (planned) | spec-only until the Phase 7 freeze (`--unfreeze-holdout` required) |
| benchmark | 7001 | 2026-05-01Z | test-only scale profile (≥500 rows), never committed |

## 11. Regeneration

```bat
.venv\Scripts\python scripts\generate_dataset.py --profile dev --seed 4104
.venv\Scripts\python scripts\generate_dataset.py --profile adversarial --seed 4105
.venv\Scripts\python scripts\check_label_isolation.py
```

The Phase 1 gate regenerates both profiles into a temp directory and
byte-compares them against the committed copies, so the committed datasets
can never drift from the generator.
