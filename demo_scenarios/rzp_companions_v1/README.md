# ARGUS realistic companion scenario v1

**SYNTHETIC_DEMO — not bank-issued evidence, not production eligible.**

This reproducible fictional merchant scenario is bound to Test Mode import
`gwi-38a22e8d7367bac0af9d` and seed `20260903`. It preserves that import's existing
labelled demo gateway snapshot byte-for-byte: 60 payments, 8 refunds and 3
simulated settlements. The underlying payment/refund IDs came from Test Mode;
the settlement membership, UTRs and downstream records are simulated, not
official Razorpay settlement responses. No official API was seeded by this work.

## What to upload now

In the same existing Import Data session, keep its three gateway demo sources.
Replace only the merchant-side uploads with:

1. Bank statement: `inputs/bank_entries.csv` — 4 physical rows.
2. Merchant ledger: `inputs/ledger_entries.csv` — 71 physical rows.

Review the column mapping before confirming. The bank upload should report
3 accepted rows and **1 quarantined row**: the `N/A` amount is intentional.
The ledger accepts 71 rows; the run subsequently identifies one as a repeated
delivery, not another economic posting.

Do not use the older March sample files in this session: their references and
UTRs belong to another fictional merchant dataset. The current **Generate
synthetic gateway evidence** action stages only payments, refunds and settlements;
it never generates or replaces bank or ledger files. Existing merchant uploads
are preserved, so a session that already has both may immediately show 3/3.
If you import a different gateway snapshot, regenerate its companions instead
of reusing these files.

Run full reconciliation, then check the exception and approval views. Do not
upload `labels/`, `manifest.json`, or `verification.json`. The three gateway CSVs
in this bundle are frozen reproduction inputs, not extra merchant documents.

An older five-file demo bundle may correctly show **PARTIALLY_ACTIVE** after
these manual uploads replace its original bank/ledger revisions. Current
gateway-only bundles track only their three gateway sources. Legacy generated
merchant files do not satisfy the separate-upload requirement. Uploading the
companion files does not make them real evidence: their descriptions, this
manifest and the demo context identify them as synthetic. Manual upload
provenance is `MANUAL_CSV`; do not call this independently verified bank receipt.

## Business events, not a target match rate

The initial companion postings follow the same payment, refund and settlement
references. Deterministic, seeded event failures are then applied independently
of reconciliation output. Reordering gateway inputs does not change the result.

| Event in the fictional merchant workflow | Expected current-engine outcome |
| --- | --- |
| ERP retry creates two journal IDs for one payment | 1 duplicate-posting case; negative correction proposal |
| Two processed refunds are absent from the ledger extract | 2 missing-refund cases; negative correction proposals |
| One payment journal is overstated by INR 1.00 | 1 unresolved ambiguous-evidence case; no invented correction |
| An export loses one payment reference | 1 unresolved ambiguous-evidence case; amount alone is insufficient |
| Latest settlement credit is absent from the bank extract | 1 unresolved ambiguous-evidence case; missing evidence is not proof of lost money |
| An unrelated fictional bank service charge of INR 1.25 appears | 1 unresolved ambiguous-evidence case; outside the gateway evidence |
| Settlement booking dates lie outside the imported collection windows | 3 timing-window cases; zero monetary delta |
| Bank export contains an unavailable amount (`N/A`) | 1 quarantined row, not silently dropped |
| An identical ledger record is delivered twice | 1 duplicate delivery, distinct from the ERP duplicate posting |

The existing settlement simulator records collection windows before its payout
dates. These companions preserve those windows and use actual simulated booking
dates; the first booking is additionally delayed two days. Consequently all three
settlements trigger the current timing rule. This is a **known simulator/window
policy limitation**, not evidence that three real bank transfers were late.
We did not backdate entries or change the engine to remove the warnings.

Many payments feed a single settlement, which maps to a bank credit through its
UTR. Ledger rows link through payment, refund or settlement references. Names,
filenames and customer names are not identity keys. This is a payment-clearing
and bank reconciliation extract, not a complete double-entry general ledger.
Account codes, fees introduced here and incident frequencies are fictional
merchant assumptions, not Razorpay or bank policy.

## Verified result and its limits

`verification.json` records an actual isolated, offline run:

- 146 raw rows: 144 eligible economic records + 1 quarantine + 1 duplicate delivery.
- 10 cases: 4 ambiguous, 1 duplicate posting, 2 missing refunds, 3 timing windows.
- All 10 expected case anchors and proposed deltas matched; no unexpected cases.
- 140 records participate in matches: about 97.22% runtime record match rate.

**Record match participation is not full financial closure or measured accuracy.**
A payment can match its settlement while a related journal exception remains.
Do not present this percentage as “97.22% of transactions fully verified.” This
is a known-fixture regression check, not held-out accuracy or a replacement for
the project's 500+ record submission benchmark. This snapshot has 60 payments;
the larger order count is not a count of captured financial events.

The offline run and upload-route regression use `fake-deterministic-v1`, not a
live Groq investigator. They verify data flow and deterministic safeguards, not
live agent behavior. Any nonzero correction still needs verifier PASS, dry-run
and explicit human approval; application is simulated. No approval was executed
while preparing these files. A real-provider demo needs separate verification.

## Reproduction and integrity

From the repository root, run the verifier without your database or credentials:

```powershell
.venv/Scripts/python.exe scripts/verify_companion_scenario.py demo_scenarios/rzp_companions_v1
.venv/Scripts/python.exe -m pytest backend/tests/unit/test_companion_scenario.py -q --basetemp tmp/companion-tests
```

The verifier checks input hashes, runs only `inputs/` against an in-memory
database, and evaluates expectations separately. Runtime receives no labels.
Tests also exercise the actual merchant-upload and reconcile-session API path
against an isolated temporary database, without network calls.

Regenerate the event matrices from the frozen snapshot into a new JSON file:

```powershell
.venv/Scripts/python.exe scripts/build_companion_scenario.py --snapshot-dir demo_scenarios/rzp_companions_v1 --seed 20260903 --output-json tmp/companion-rebuild/scenario.json
```

For a new labelled active demo, use `--session-dir` and `--import-id` instead;
the builder verifies active source provenance and hashes before reading. It
refuses snapshots too small for these scenarios rather than inventing payments.

`scripts/export_companion_scenario.mjs` authors the two final CSVs using
`@oai/artifact-tool`, preserving exact string amounts and identifiers with a
cell round-trip check. Run it in a Node authoring environment where that package
is resolvable, with the JSON path and a **new** output directory as arguments.
The package is an authoring dependency, not an ARGUS runtime requirement. The
exporter copies gateway CSVs unchanged, writes hashes and separate evaluator
labels, and rejects overwrite of a completed bundle. This checked-in bundle can
be used and verified without that authoring package or the original session.

## Suggested demo wording

“These payment and refund identifiers originate in Razorpay Test Mode. Settlement,
bank and accounting evidence here is a labelled synthetic merchant scenario.
We simulate ERP retries, missing records and timing differences. ARGUS shows
which links it can prove, which corrections need approval, and which cases must
remain unresolved. In production, bank and ledger evidence must come independently
from those systems; generating it from gateway data cannot prove cash receipt.”

This slice changes no matching rules, UI, live session, production system, or
original sample files. Owner review comes before any subsequent implementation.
