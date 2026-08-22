# ARGUS CONTROL

> **Razorpay AI Buildathon 2026 — Track 04: AI Finance Controller**
>
> **Product:** A financial flight recorder for merchant reconciliation.
>
> **Promise:** ARGUS reconstructs the evidence path behind recorded amounts, investigates broken links, produces proof-carrying correction proposals, and refuses to close cases it cannot verify.

## 1. Repository Status

This repository is currently in the specification stage. The documents describe the product that must be implemented; they do not claim that the application, integrations, benchmark results, or performance numbers already exist.

The specification is intentionally scoped for a Buildathon submission. The mandatory build is a narrow, measurable vertical slice. Enterprise features such as broad ERP integrations, realtime voice, vector memory, distributed workers, and unrestricted autonomous posting are not part of the required MVP.

The three governing documents are:

- `README_ARGUS_CONTROL.md` — product description, scope, demo, and success definition.
- `ARGUS_CONTROL_PRD.md` — authoritative functional, engineering, data, safety, and evaluation requirements.
- `ARGUS_CONTROL_MASTER_PROMPT.md` — execution rules for a coding agent working phase by phase.

If the documents disagree, use this precedence:

1. Safety and financial integrity rules in the PRD.
2. Frozen MVP contract in the PRD.
3. Phase acceptance gate in the PRD.
4. Master prompt implementation guidance.
5. README narrative.

## 2. The Product in One Sentence

> **ARGUS CONTROL is an evaluation-first financial exception investigator that deterministically reconciles merchant payment records, uses AI to investigate only the remaining exceptions, verifies every proposed explanation with structured rules, previews the ledger effect, and escalates ambiguity with a complete evidence package.**

## 3. Why This Exists

Merchants commonly need to connect several representations of the same financial activity:

- payment-gateway payments;
- refunds and adjustments;
- settlement records;
- bank credits;
- merchant-ledger entries;
- fees and taxes;
- accounting-period boundaries.

Straightforward cases should be matched with identifiers and arithmetic. They do not need an LLM.

The expensive work begins when the records disagree:

```text
Expected net settlement:  ₹78,640
Observed bank credit:      ₹72,240
Unexplained variance:       ₹6,400
```

A finance operator must determine whether the difference is caused by a missing refund posting, a duplicate ledger entry, a settlement-window shift, or incomplete evidence. The operator must inspect multiple files, test competing explanations, document the conclusion, and decide whether a correction is safe.

ARGUS automates that bounded investigation loop. It is not a general bookkeeping replacement and it does not move real money.

## 4. Track Alignment

Track 04 asks builders to run one finance-operations loop across at least 50 synthetic records, report the match rate, report measured accuracy and throughput, and provide an honest exception list.

ARGUS closes this loop:

```text
Payments + refunds + settlements + bank entries + merchant ledger
                              ↓
                    normalize and validate
                              ↓
                 deterministic reconciliation
                              ↓
                   matched records + cases
                              ↓
                   evidence-driven investigation
                              ↓
                    deterministic verification
                              ↓
        resolved | approval required | unresolved
                              ↓
              ledger dry-run + append-only audit
                              ↓
             measured benchmark and exception list
```

## 5. Finalized Product Positioning

ARGUS must not be presented as the first AI reconciliation product. Reconciliation tools, settlement reports, and finance automation products already exist.

The Buildathon differentiation is narrower:

### 5.1 Financial flight recorder

ARGUS renders an evidence graph connecting payment, refund, settlement, bank, and ledger events. It shows which connections are proven, which are broken, and which are merely hypotheses.

### 5.2 Proof-carrying corrections

Every proposed correction carries a machine-readable proof package containing:

- the claim;
- the affected records;
- the exact financial equation;
- the hypotheses tested;
- the hypotheses rejected and why;
- the deterministic verifier result;
- the proposed ledger delta;
- the authority decision;
- remaining uncertainty;
- audit-event identifiers.

### 5.3 Falsification before acceptance

The investigator does not search only for a plausible answer. It must enumerate competing hypotheses. Deterministic code attempts to disprove them. A plausible narrative without a passing verifier cannot resolve a case.

### 5.4 Ledger dry-run

Before approval, ARGUS shows the internal ledger state before and after a proposed correction. This is a simulation against the prototype ledger. It is not a prediction of external bank, gateway, tax, or ERP state.

### 5.5 Honest quarantine

If evidence does not uniquely identify one valid explanation, the case remains unresolved. ARGUS must state what evidence is missing and what a human should inspect next.

### 5.6 ARGUS Voice Control Layer

Voice is an optional supervision surface, not an accounting engine or an identity mechanism. After the mandatory benchmark passes, a finance controller may use push-to-talk commands to start a safe workflow, navigate cases, request an explanation, or prepare already-verified corrections.

Voice MUST NOT approve or apply a correction. A spoken request such as “approve everything” must be refused and redirected to the visible approval screen. This refusal is part of the winning demo because it proves bounded authority rather than merely adding speech to a chatbot.

Recommended commands:

```text
“ARGUS, close today’s batch.”
“ARGUS, open presentation mode.”
“Show me the highest-value unresolved case.”
“Why is case 42 unresolved?”
“Prepare previews for verified corrections below ₹10,000.”
“What evidence is missing?”
```

`OPEN_PRESENTATION_MODE` should navigate to a known in-app presentation route. It should not grant arbitrary access to local files or desktop applications.

### 5.7 Multilingual Bharat Mode

The Voice Control Layer should understand and respond in the language selected by the user while converting every command into the same canonical intent schema. Language changes presentation, transcription, and explanation; it never changes tool authority or financial rules.

Evaluated MVP languages:

- English (`en-IN`);
- Hindi and Hinglish (`hi-IN` plus code-mixed speech);
- Kannada (`kn-IN`);
- Tamil (`ta-IN`);
- Telugu (`te-IN`).

Stretch languages use the same provider interface and may include Marathi, Bengali, Gujarati, Malayalam, Punjabi, and other scheduled Indian languages. The project must not claim support for a language until its intent, entity, refusal, and spoken-output test pack passes.

Example:

```text
User: “ARGUS, aaj ka reconciliation chalao.”
Canonical intent: RUN_RECONCILIATION
Canonical arguments: {"dataset_scope": "loaded_demo_batch"}

User: “Case bayālīs unresolved kyun hai?”
Canonical intent: EXPLAIN_CASE
Canonical arguments: {"case_id": "CASE-0042"}

User: “ಹತ್ತು ಸಾವಿರ ರೂಪಾಯಿಗಿಂತ ಕಡಿಮೆ ಇರುವ verified corrections ತಯಾರಿಸು.”
Canonical intent: PREPARE_VERIFIED_CORRECTION_PREVIEWS
Canonical arguments: {"maximum_amount_paise": 1000000}
```

Before any action with an amount or case identifier, the UI displays the original transcript, detected language, normalized value, and canonical intent. Low-confidence or conflicting entity extraction must ask for clarification rather than execute.

## 6. What ARGUS Can and Cannot Prove

ARGUS can prove that a proposed explanation is consistent with:

- the input records available to the run;
- the configured financial equations;
- the configured matching windows;
- the prototype merchant policy;
- the frozen verifier implementation.

ARGUS cannot prove:

- that all source records are complete or truthful;
- that a reconciled transaction is legitimate rather than fraudulent;
- that a zero variance means every accounting treatment is correct;
- that an external ERP, bank, or payment gateway will accept a correction;
- compliance with every accounting, tax, or regulatory requirement;
- the physical path of an individual rupee.

Use the phrase **evidence path behind a recorded amount**. Do not claim to trace an individual physical rupee.

## 7. Primary User and Economic Boundary

The intended user is a finance controller or reconciliation analyst at a merchant with enough transaction volume and source-system complexity to create repeated exceptions.

Most plausible initial segment:

- Indian D2C, marketplace, education, travel, or subscription business;
- multiple payment/refund channels;
- tens of thousands of monthly transactions rather than a few hundred;
- a small finance team using spreadsheets, Tally, Zoho Books, NetSuite, or another ledger;
- recurring settlement, refund, or cutoff mismatches.

ARGUS is unlikely to create enough value for a very small merchant using one payment gateway and a simple settlement report.

The business hypothesis is not considered validated by the Buildathon. Commercial validation later requires interviews, real anonymized exception samples, measured hours saved, and evidence of willingness to pay.

## 8. Frozen MVP

The mandatory MVP contains exactly one finance-operations loop and four exception classes.

### 8.1 Inputs

- payment records;
- refund records;
- settlement records;
- bank-statement entries;
- merchant-ledger entries;
- merchant authority configuration;
- labelled synthetic ground truth used only by evaluation.

### 8.2 Exception classes

1. `DUPLICATE_LEDGER_POSTING`
   - The same source-side financial event is posted more than once in the ledger.

2. `MISSING_REFUND_POSTING`
   - A verified refund or refund adjustment is absent from the merchant ledger.

3. `SETTLEMENT_TIMING_WINDOW_SHIFT`
   - A valid transaction belongs to a different settlement or accounting window.

4. `AMBIGUOUS_EVIDENCE`
   - Two or more explanations remain consistent with the available records. The only valid final state is unresolved.

Fee and tax calculations remain part of deterministic normalization and reconciliation. A separate fee/tax exception class may be added only after the four mandatory classes pass the frozen holdout.

### 8.3 Required outcomes

Every case ends in exactly one of these outcomes:

- `VERIFIED_RESOLVED`
- `APPROVAL_REQUIRED`
- `SIMULATED_APPLIED`
- `UNRESOLVED`
- `INVESTIGATION_FAILED`

No case may be marked resolved directly by the model.

### 8.4 Required dataset sizes

- unit fixtures: minimal records needed for each rule;
- development batch: at least 100 eligible records;
- submission benchmark: at least 500 eligible records if performance permits, never fewer than the track minimum of 50;
- frozen holdout: generated or authored independently from the tuning fixtures and never exposed to the investigator.

## 9. Core Safety Invariants

1. Use integer paise for all INR amounts.
2. Do not use binary floating-point arithmetic for money.
3. Every imported source row receives a stable source identifier and content hash.
4. Every run is idempotent for the same tenant, dataset, and configuration.
5. The model cannot write directly to financial tables.
6. The model cannot mark a case resolved.
7. Every resolution requires a deterministic verifier `PASS`.
8. Every resolution must cite source record identifiers.
9. A ledger correction is first represented as a dry-run delta.
10. A simulated application requires explicit authority or human approval.
11. An ambiguous case must remain unresolved.
12. Missing evidence is never invented.
13. Ground-truth labels are unavailable to runtime services.
14. Policy values used in the demo are labelled synthetic merchant policy, not Razorpay policy.
15. The prototype does not move real money.
16. Razorpay integrations use documented Test Mode or public read APIs only.
17. Model errors, timeouts, or malformed outputs become controlled failure states.
18. Uploaded text and narrations are treated as untrusted data, not instructions.
19. No benchmark number is published until produced by the benchmark runner.
20. Reconciliation proves record consistency, not fraud absence or regulatory compliance.

## 10. Minimal Architecture

The required architecture should remain boring where boring improves trust.

```text
Next.js control-room UI
        ↓ HTTP/SSE
FastAPI application
        ├── import and normalization
        ├── deterministic reconciliation
        ├── evidence graph builder
        ├── exception case service
        ├── structured investigator tools
        ├── deterministic verifier
        ├── policy/approval service
        ├── ledger dry-run service
        ├── append-only audit service
        └── benchmark runner
        ↓
SQLite for default local demo or PostgreSQL when configured
```

One investigator is sufficient. A second model agent is not required. The deterministic verifier is code, not another conversational agent.

### 10.1 Suggested stack

- frontend: Next.js, React, TypeScript, Tailwind CSS;
- evidence graph: React Flow, Cytoscape.js, or another lightweight graph renderer;
- backend: Python, FastAPI, Pydantic;
- persistence: SQLite for zero-friction local use with a clean repository interface; optional PostgreSQL deployment adapter;
- tests: pytest, Hypothesis where financial invariants benefit from generated inputs;
- frontend tests: Vitest and Playwright;
- AI: a configurable structured-output model behind a provider interface;
- optional voice: push-to-talk transcription mapped to a constrained intent schema, with browser or provider speech output;
- multilingual speech default: Sarvam AI for evaluated Indic STT/TTS and code-mixed speech;
- multilingual speech fallback: OpenAI transcription and speech APIs behind the same interface;
- optional self-hosted translation: AI4Bharat IndicTrans2;
- optional public-language-platform adapter: BHASHINI, only when access is available;
- updates: polling first, SSE only if it remains simple;
- packaging: Docker Compose only if it improves reproducibility rather than adding setup risk.

Redis, pgvector, full RAG, always-on realtime voice, and distributed workers are not mandatory dependencies.

## 11. Evidence Graph

The graph contains typed nodes:

- `PAYMENT`
- `REFUND`
- `SETTLEMENT`
- `BANK_ENTRY`
- `LEDGER_ENTRY`
- `FEE`
- `TAX`
- `CORRECTION_PROPOSAL`

Edges contain:

- relationship type;
- confidence source: `EXACT`, `RULE`, `HYPOTHESIS`, or `REJECTED`;
- evidence identifiers;
- amount contribution;
- verifier status;
- explanation.

Visual semantics:

- green: deterministically proven;
- amber: proposed hypothesis;
- red: broken, rejected, or inconsistent;
- grey: unexamined or out of scope;
- purple: proposed ledger correction.

The graph is a user interface over explicit data. It must not imply certainty beyond the stored edge status.

## 12. Proof Package

Canonical example:

```json
{
  "case_id": "CASE-0042",
  "claim": "A duplicate merchant-ledger posting explains the variance.",
  "category": "DUPLICATE_LEDGER_POSTING",
  "evidence_ids": ["PAY-0188", "SET-0017", "BANK-0039", "LEDGER-0811", "LEDGER-0812"],
  "equations": [
    {
      "expression": "expected_net_paise - observed_bank_credit_paise",
      "inputs": {"expected_net_paise": 7864000, "observed_bank_credit_paise": 7224000},
      "result_paise": 640000
    }
  ],
  "hypotheses_tested": [
    {"type": "DUPLICATE_LEDGER_POSTING", "result": "SUPPORTED"},
    {"type": "SETTLEMENT_TIMING_WINDOW_SHIFT", "result": "REJECTED", "reason_code": "NO_VALID_ADJACENT_WINDOW"}
  ],
  "verifier": {"status": "PASS", "rule_version": "duplicate-ledger-v1"},
  "proposed_delta": {"ledger_entry_id": "LEDGER-0812", "amount_paise": -640000},
  "dry_run": {"variance_before_paise": 640000, "variance_after_paise": 0},
  "authority": {"decision": "APPROVAL_REQUIRED", "policy_version": "demo-policy-v1"},
  "uncertainty": [],
  "audit_event_ids": ["AUD-0901", "AUD-0902"]
}
```

The model may propose the claim and select tools. Code calculates equations, validates identifiers, determines verifier status, computes the dry-run, and applies authority rules.

## 13. Evaluation Philosophy

ARGUS is an evaluation-first project. A feature is incomplete until its failure behaviour is tested.

### 13.1 Mandatory metrics

- deterministic match rate;
- deterministic match precision;
- exception-classification accuracy;
- resolved-exception precision;
- false-resolution count and rate;
- correct escalation rate for ambiguous cases;
- money-weighted residual error;
- records processed per second;
- median and P95 investigation latency;
- audit completeness;
- model calls and estimated cost per investigated case;
- unresolved exception list.

### 13.2 Metric priority

The ranking is:

1. zero or minimal false financial resolutions;
2. correct escalation of ambiguous evidence;
3. financial-total accuracy;
4. audit completeness;
5. exception-resolution coverage;
6. throughput and latency;
7. model cost;

High automation with incorrect closures is worse than lower automation with honest escalation.

### 13.3 Anti-overfitting rules

- Runtime code cannot read ground-truth labels.
- Development and holdout seeds are different.
- Holdout labels are frozen before final prompt tuning.
- At least one holdout fixture must be authored or transformed independently of the main generator.
- Holdout files vary ordering, optional fields, date formats, and harmless column names.
- After the benchmark is frozen, fixes caused by holdout failures require a documented regression test and a new benchmark version.
- The final report states the dataset construction limitations.

## 14. Nine-Phase Build Plan

Each phase has a detailed evaluation gate in the PRD. A phase is not complete because code exists; it is complete only when its commands pass and its evidence artifacts are recorded.

| Phase | Deliverable | Required gate |
|---|---|---|
| 0 | Repository foundation and frozen contracts | clean install, lint, unit-test smoke, no secret leaks |
| 1 | Synthetic data and ground truth | deterministic generation, accounting conservation, isolated labels |
| 2 | Deterministic reconciliation and evidence graph | exact expected matches, idempotency, no silent row loss |
| 3 | Exception verifier and proof packages | every valid proof passes; adversarial and ambiguous proofs fail safely |
| 4 | AI investigator with structured tools | model cannot bypass tools or verifier; controlled timeout/malformed output |
| 5 | Control-room UI, ledger dry-run, approval, audit | full golden path and unresolved path pass end to end |
| 6 | Failure laboratory and optional Razorpay Test Mode adapter | duplicate/out-of-order events recover without financial duplication |
| 7 | Frozen holdout benchmark and hardening | reproducible report, zero hidden labels, honest limitations |
| 8 | Submission release | fresh-clone rehearsal, five-minute demo, public documentation |

## 15. Golden Vertical Slice

Build this before optional integrations or visual polish:

```text
Load 100 labelled development records
→ validate and normalize
→ deterministically reconcile clean records
→ create one duplicate-ledger case
→ investigator inspects structured evidence
→ investigator proposes competing hypotheses
→ verifier rejects unsupported alternatives
→ verifier passes the duplicate-ledger proof
→ dry-run reduces the internal variance to zero
→ authority requires human approval
→ user approves simulated application
→ case changes to SIMULATED_APPLIED
→ audit trail contains the complete proof package
→ a second ambiguous case remains UNRESOLVED
```

If this flow does not work through backend APIs, do not build voice, animation, or additional agents.

## 16. Five-Minute Demo

### 0:00–0:30 — Establish the pain

Show a ₹6,400 mismatch across payment, refund, settlement, bank, and ledger records.

### 0:30–1:00 — Run the batch by voice

Say “ARGUS, close today’s batch.” Display the transcript and the exact parsed intent before the existing run action executes. Show measured match rate, unresolved cases, financial variance, and throughput. If voice fails, use the identical typed command without changing the workflow.

### 1:00–2:15 — Replay the evidence

Open one case. Animate or step through the evidence graph. Show competing hypotheses and why unsupported explanations fail.

### 2:15–3:00 — Preview and approve

Say “Prepare previews for verified corrections below ₹10,000.” Show variance before and after the ledger dry-run. Then say “Approve everything.” ARGUS must refuse because voice is not an approval channel. Approve the exact simulated correction through the visible control-room confirmation.

### 3:00–3:35 — Break the system deliberately

Inject a duplicate or out-of-order event. Show idempotency and recovery without a duplicate financial effect.

### 3:35–4:10 — Refuse to guess

Open an ambiguous case. ARGUS must state that two candidates remain valid, identify the missing unique reference, and escalate.

### 4:10–4:45 — Show the benchmark

Display real metrics, false resolutions, correct escalations, cost, and the unresolved list.

### 4:45–5:00 — Close

> “ARGUS does not ask AI to calculate the books. It asks AI where to investigate, and demands proof before changing anything.”

## 17. Explicit Non-Goals

The submission does not:

- move live money;
- write to a production ERP;
- replace an accountant or auditor;
- perform fraud detection;
- guarantee tax or regulatory compliance;
- trace physical currency units;
- predict external-system state;
- solve accounts payable, cash forecasting, disputes, or revenue recovery;
- support every file format or ERP;
- claim commercial validation;
- claim production readiness inside Razorpay;
- require voice, RAG, vector memory, or multi-agent orchestration.

## 18. Optional Features, in Strict Order

Optional work is allowed only after Phase 7 passes:

1. polished evidence-graph transitions;
2. typed natural-language controller commands;
3. the ARGUS Voice Control Layer using the same constrained typed-command contract;
4. a second independently authored holdout pack;
5. one additional exception class;
6. a real Razorpay Test Mode import path if credentials and API coverage permit;
7. deployment hardening;
8. persistent merchant preferences;
9. policy-document retrieval;
10. specialist agents only if an evaluation proves measurable benefit.

## 19. Submission Definition of Done

- [ ] A fresh clone can be installed from documented commands.
- [ ] The default demo runs without private production data.
- [ ] At least 50 eligible records are processed; target 500.
- [ ] All four mandatory exception classes exist in the benchmark.
- [ ] Clean matches are deterministic and idempotent.
- [ ] Runtime services cannot access ground-truth labels.
- [ ] An investigator uses only structured read/calculation/proposal tools.
- [ ] A model cannot directly resolve a case or mutate the ledger.
- [ ] Every resolution has a verifier `PASS` and evidence identifiers.
- [ ] A ledger correction is shown as a dry-run before approval.
- [ ] Approval and simulated application are auditable.
- [ ] At least one ambiguous case remains unresolved for the correct reason.
- [ ] A duplicate or out-of-order event is recovered safely.
- [ ] The final holdout benchmark is frozen and reproducible.
- [ ] False-resolution count is prominently reported.
- [ ] The unresolved exception list is downloadable or visible.
- [ ] No placeholder metric is presented as measured.
- [ ] No API, policy, partnership, or platform access is invented.
- [ ] The five-minute pitch has a rehearsed backup recording.
- [ ] Limitations and unvalidated business assumptions are explicit.

## 20. Final Product Statement

> **ARGUS CONTROL is a financial flight recorder for merchant reconciliation. It builds a typed evidence graph across payments, refunds, settlements, bank credits, and ledger entries; reconciles straightforward records with deterministic code; uses one bounded AI investigator to test competing explanations for the residual cases; requires a deterministic proof package before any resolution; previews corrections against a sandbox ledger; enforces authority and approval; recovers from duplicate or disordered events; and leaves cases unresolved when the evidence cannot support a unique conclusion.**
