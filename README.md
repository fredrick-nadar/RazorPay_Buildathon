# ARGUS CONTROL

**A financial flight recorder for merchant reconciliation.**
Razorpay AI Buildathon 2026 — Track 04. Prototype. Synthetic data only.

ARGUS deterministically reconciles payments, refunds, settlements, bank credits
and merchant-ledger entries in signed integer paise; sends only the residual
discrepancies to one bounded AI investigator; requires a deterministic verifier
`PASS` with a complete proof package before any case is resolved; previews every
ledger effect as a dry run; requires human approval for every nonzero
correction; applies corrections only as new linked **simulated** entries; and
leaves ambiguous cases unresolved.

> **Rules for calculation, AI for investigation, verification for closure,
> approval for authority, humans for ambiguity.**

No real money moves. No production ERP is written. The ARGUS application uses
documented **Test Mode, read-only** Razorpay imports. The optional owner-only
demo seeder writes synthetic entities to Razorpay Test Mode and is never part
of the reconciliation runtime.

## Documents

**Governing specifications** (authoritative; not modified by implementation work):

- [`README_ARGUS_CONTROL.md`](README_ARGUS_CONTROL.md) — product narrative
- [`ARGUS_CONTROL_PRD.md`](ARGUS_CONTROL_PRD.md) — requirements, phases, gates
- [`ARGUS_CONTROL_MASTER_PROMPT.md`](ARGUS_CONTROL_MASTER_PROMPT.md) — implementation guidance
- [`AGENTS.md`](AGENTS.md) — permanent contributor rules
- [`BUILD_STATUS.md`](BUILD_STATUS.md) — living phase status

**Implementation documentation**:

- [`docs/architecture.md`](docs/architecture.md) — process, storage and authority boundaries
- [`docs/data-flow.md`](docs/data-flow.md) — intake paths, run pipeline, what leaves the machine
- [`docs/security-and-deployment.md`](docs/security-and-deployment.md) — CORS, persistence, restart/restore, secrets, limitations
- [`docs/reconciliation_rules.md`](docs/reconciliation_rules.md), [`docs/verification_rules.md`](docs/verification_rules.md), [`docs/investigator_rules.md`](docs/investigator_rules.md), [`docs/data_dictionary.md`](docs/data_dictionary.md)

## Requirements

Python 3.12, Node.js 22 with npm 11, git. Windows commands are shown; the
POSIX equivalents differ only in the venv path (`.venv/bin/python`).

## Bootstrap (fresh clone)

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -r backend/requirements.lock.txt
```

```bash
cd frontend && npm ci
```

Optional, only for Playwright end-to-end tests:

```bash
cd frontend && npx playwright install chromium
```

No configuration is required. With no environment variables set, ARGUS starts
in **rules-only** mode against a local SQLite database. Copy `.env.example` to
`.env.local` (gitignored) only if you want optional model, Razorpay Test Mode
or voice features; it contains variable **names only**.

## Run

Backend (one process — see
[`docs/security-and-deployment.md`](docs/security-and-deployment.md#1-supported-deployment-topology)):

```bash
.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Frontend:

```bash
npm --prefix frontend run dev
```

Open <http://localhost:3000/dashboard>. The presentation view is at
<http://localhost:3000/presentation>.

## Tests and checks

```bash
.venv\Scripts\python -m pytest backend/tests -q
```

```bash
.venv\Scripts\python -m ruff check backend
```

```bash
.venv\Scripts\python -m ruff format --check backend
```

```bash
cd backend && ..\.venv\Scripts\python -m mypy
```

```bash
npm --prefix frontend run lint
```

```bash
npm --prefix frontend run typecheck
```

```bash
npm --prefix frontend run test
```

```bash
npm --prefix frontend run build
```

```bash
npm --prefix frontend run test:e2e
```

Label firewall and repository hygiene:

```bash
.venv\Scripts\python scripts/check_label_isolation.py
```

## Phase gates

`scripts/verify_phase.py` is the authoritative acceptance gate. It never
installs or downloads dependencies: it fails fast with a bootstrap hint when
they are missing, and writes `artifacts/evaluation/phase-NN.json` from actual
command outcomes.

```bash
.venv\Scripts\python scripts/verify_phase.py --phase 7
```

```bash
.venv\Scripts\python scripts/verify_phase.py --phase 8
```

Phase 8 is the release gate: full backend suite, lint/format/strict mypy,
frontend lint/typecheck/unit/build, startup health probes, dataset-generation
smoke, rules-only benchmark smoke, label firewall, secret scan, gitignore
coverage, release-document presence and link validity, benchmark-artifact
consistency, rules-only fallback, persistent restart/migration, fresh-checkout
readiness, and required submission assets.

## Benchmark evidence

The numbers below are **historical committed evidence** from the Phase 7
holdout run recorded in
[`artifacts/benchmark/final.json`](artifacts/benchmark/final.json) and
[`artifacts/benchmark/final_summary.md`](artifacts/benchmark/final_summary.md).
They were produced by the benchmark runner, not typed by hand. They are **not**
a fresh measurement of the current working tree.

| Measure | Committed Phase 7 value | Numerator / denominator |
| --- | --- | --- |
| Eligible canonical records | 1,880 | — |
| Match precision | 1.0 | 1,124 / 1,124 |
| Record match rate | 0.991489 | 1,864 / 1,880 |
| Case classification accuracy | 1.0 | 23 / 23 |
| False verifier passes | 0 | 0 / 23 |
| Money-weighted dry-run error | 0 paise | — |
| Proof completeness | complete | 18 / 18 |
| Ambiguous escalation preserved | 1.0 | 5 / 5 |
| Throughput | 9,656.10 rec/s | 1,880 records / 0.194696 s end-to-end |

**A final release rerun has not been performed.** Before submission the owner
reruns the benchmark on the frozen holdout and republishes these values from
the regenerated artifacts. Any figure quoted anywhere in the submission must
come from the benchmark runner's own output; a target value is never presented
as a result.

The table above comes from the **agent-mode** run against the frozen holdout.
The exact invocation that produced it is the one Phase 7 runs
(`run_phase7_steps` in `scripts/verify_phase.py`); re-running it **overwrites**
`artifacts/benchmark/final.json`, so run it only when you intend to republish
the committed evidence:

```bash
.venv\Scripts\python scripts/run_benchmark.py --dataset datasets/holdout --mode agent --provider fake --output artifacts/benchmark/final.json
```

To check the pipeline without touching committed evidence, run the rules-only
smoke to a scratch output instead. This is a **reproduction smoke, not the
final committed measurement** — it exercises the deterministic path only and
reports its own separate throughput:

```bash
.venv\Scripts\python scripts/run_benchmark.py --dataset datasets/holdout --mode rules-only --output tmp/holdout-rules-only-smoke.json
```

## Demo

The five-minute demo script and rehearsal checks are specified in
`ARGUS_CONTROL_PRD.md` (Phase 8). Deterministic demo data and a fallback batch
run are available without any model credential:

```bash
.venv\Scripts\python scripts/generate_dataset.py --profile dev --seed 4104
```

```bash
.venv\Scripts\python scripts/seed_local_e2e_fixture.py
```

### Create the official Razorpay Test Mode demo batch

This is optional and makes external network requests to Razorpay Test Mode.
Keep `ARGUS_RAZORPAY_KEY_ID` and `ARGUS_RAZORPAY_KEY_SECRET` only in the
gitignored `.env.local`; never commit or share the secret.

ARGUS has two deliberately separate seed utilities:

- `scripts/seed_razorpay_test_data.py` creates **order intents** through the
  official Test Mode Orders API. Orders are not payments and cannot be
  reconciled as captured financial events.
- `scripts/seed_razorpay_test_lifecycle.py` selects suitable unused orders,
  presents them one at a time through Razorpay Standard Checkout, verifies the
  returned signature, captures authorized payments, records resumable progress
  without credentials, creates a bounded refund subset, and verifies what the
  official read APIs return.

The demo account already contains 549 orders, so do **not** run the order
inventory command again for the current demo. For a new empty Test Mode account,
run it once; every invocation creates another batch of up to 500 orders:

```bash
.venv\Scripts\python scripts/seed_razorpay_test_data.py
```

To produce the demonstrated 60 captured payments, start the local Checkout
coordinator:

```bash
.venv\Scripts\python scripts/seed_razorpay_test_lifecycle.py serve-checkout --count 60 --port 8765
```

Open <http://127.0.0.1:8765/> and complete each Test Mode Checkout. This human
Checkout step is required: Razorpay does not provide a backend Payments API for
inserting arbitrary captured payments. The coordinator automatically verifies
each Checkout signature, captures an authorized payment when necessary,
recovers a paid order if the browser callback is lost, and resumes from
`tmp/razorpay_test_lifecycle_state.json` after a restart.

When the page reaches `60/60 captured`, stop the coordinator and create the
eight controlled Test Mode refunds used by the demo:

```bash
.venv\Scripts\python scripts/seed_razorpay_test_lifecycle.py create-refunds --count 8
```

Verify that Razorpay's official Orders, Payments, Refunds, Settlements and
Settlement Reconciliation reads are reachable and record their returned
counts:

```bash
.venv\Scripts\python scripts/seed_razorpay_test_lifecycle.py verify
```

Finally, start ARGUS, open **Import Data**, enter the same Test Mode credentials
as request-scoped values, and choose a date range containing those payments.
The owner's recorded demo account returned 549 orders, 62 payments and 8
refunds; 60 of those payments formed the eligible bounded demo batch. Treat
these as that account's observed counts, not hard-coded product guarantees.
Settlement and reconciliation counts may remain zero because Razorpay owns and
processes those feeds. ARGUS never relabels locally generated settlement
evidence as Razorpay-issued evidence. Upload the matching synthetic bank and
merchant-ledger companion CSVs separately before reconciliation.

The accurate demo description is: **“ARGUS created the order inventory through
Razorpay's official Test Mode Orders API. I completed 60 legitimate Test Mode
Checkout payments; ARGUS verified, captured, tracked and retrieved them, then
created eight controlled refunds.”**

Primary and backup demo recordings are **owner-supplied release assets**; they
do not exist in this repository yet, and Phase 8 correctly fails until they and
the release manifest described in
[`docs/security-and-deployment.md`](docs/security-and-deployment.md#6-release-submission-manifest)
are present.

## Status and limitations

See [`BUILD_STATUS.md`](BUILD_STATUS.md) for the current phase status and
[`docs/security-and-deployment.md`](docs/security-and-deployment.md#7-known-limitations)
for known limitations. In short: this is a single-process prototype on SQLite,
using synthetic data only, with Test Mode read-only Razorpay access and
simulated corrections.
