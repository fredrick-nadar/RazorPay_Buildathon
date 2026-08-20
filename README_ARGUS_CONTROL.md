# ARGUS CONTROL

> **Razorpay Buildathon — Track 04: AI Finance Controller**  
> **Vision:** A voice-supervised, policy-aware AI finance controller that reconciles multi-source financial records, investigates the exceptions deterministic systems cannot resolve, remembers merchant-specific operating context, and escalates only the cases that genuinely require human judgment.

---

## 1. Buildathon Track

### Track 04 — AI Finance Controller

**Track objective provided by Razorpay:**

> Run the books and the cash position.

The track requires an agent that closes one finance-operations loop across a **50+ record batch of synthetic data**, reports its **match rate**, and provides an **honest exception list** for the cases it could not resolve.

The example directions given for the track include:

- Multi-source reconciliation
- Settlement Q&A agent
- Forward cash forecaster
- Tax-line matcher

The evaluation bar explicitly emphasizes:

- Throughput
- Measured accuracy
- Match rate
- Exceptions that remain unresolved
- Verification over cherry-picked demonstrations

ARGUS CONTROL is being designed specifically for this track.

---

## 2. What We Are Building

ARGUS CONTROL is intended to be an **AI Finance Controller for Razorpay merchants**.

The end user is the merchant's finance team, such as:

- Finance Controller
- CFO / Finance Lead
- Accounts team
- Reconciliation team
- Treasury / settlement operations team

The product is envisioned as a Razorpay-native finance operations layer that could eventually sit inside the Razorpay/RazorpayX ecosystem and help merchants understand and close their financial operations around payments, settlements, refunds, fees, taxes, bank credits, and ledger entries.

For the Buildathon prototype, we will **not assume private or privileged access to Razorpay's internal backend**.

The prototype will work only with:

- Publicly available or test/sandbox Razorpay capabilities where applicable
- Synthetic Razorpay-like payment and settlement data
- Synthetic merchant bank statement data
- Synthetic merchant ledger/accounting data
- Synthetic refund, fee, tax, and settlement records
- Merchant-defined finance policies created specifically for the prototype

The Buildathon implementation will therefore demonstrate the product vision without claiming access to production Razorpay systems.

---

## 3. Core Problem

Finance operations often contain a large number of records that are straightforward to reconcile and a smaller number of records that remain unresolved because the expected and actual financial states do not line up cleanly.

A normal reconciliation system can match records when identifiers and amounts line up directly.

Example:

```text
Payment Amount        ₹10,000
Fees                     ₹200
GST                       ₹36
Expected Settlement    ₹9,764

Actual Settlement      ₹9,764

Result: MATCH
```

This does not require an LLM.

The harder problem begins after deterministic matching has finished.

Example:

```text
Merchant Ledger Expected: ₹78,640
Bank Credit:              ₹72,240

Unexplained Difference:    ₹6,400
```

At this point, the finance team has to investigate the exception.

Possible explanations may include:

- Refund adjustment
- Partial refund
- Settlement timing difference
- Fee or GST treatment
- Duplicate ledger entry
- Missing ledger entry
- A transaction belonging to another settlement batch
- Incorrect mapping between records
- Multiple payments grouped into one settlement
- Unresolved or incomplete financial evidence

ARGUS CONTROL is intended to automate this **exception-investigation layer** while keeping all final financial checks deterministic and auditable.

---

## 4. Central Design Principle

ARGUS CONTROL is **not**:

> "Let an LLM calculate the books."

The intended architecture is:

> **Deterministic finance logic calculates and verifies. AI agents investigate, reason, orchestrate, explain, and propose or execute bounded actions. Voice supervises the system.**

This separation is fundamental to the project.

---

## 5. End-to-End Finance Operations Loop

```text
Financial Data Sources
        ↓
Normalization
        ↓
Deterministic Reconciliation
        ↓
Matched Records + Exceptions
        ↓
ARGUS Agent Orchestrator
        ↓
Specialist Exception Investigation
        ↓
Policy + Persistent Merchant Context
        ↓
Evidence-Based Resolution
        ↓
Deterministic Verification
        ↓
Auto-Close / Prepare Correction / Escalate
        ↓
Audit Trail
        ↓
Finance Controller Summary
```

---

## 6. Data Sources in the Prototype

### 6.1 Razorpay-side synthetic records

Planned record types include:

- Payments
- Orders
- Captures
- Refunds
- Settlement records
- Settlement IDs
- Fees
- GST / tax on fees
- Payment IDs
- Refund IDs
- Settlement UTRs
- Timestamps
- Payment method metadata where useful for reconciliation

### 6.2 Merchant-side synthetic records

Planned record types include:

- Bank statement entries
- Merchant accounting ledger entries
- Journal entries
- Internal transaction references
- Accounting dates
- Expected settlement amounts
- Recorded fees
- Recorded tax
- Recorded refunds

### 6.3 Ground-truth dataset

The project will include a labelled synthetic dataset so that ARGUS can be measured objectively.

Each exception case will have a known expected explanation and expected resolution status.

The ground truth will allow us to calculate actual metrics instead of manually selecting successful examples.

---

## 7. Stage One — Deterministic Reconciliation Engine

The first stage is intentionally non-agentic.

Its job is to resolve everything that can be proven directly.

Possible matching keys include:

- `payment_id`
- `order_id`
- `refund_id`
- `settlement_id`
- `UTR`
- Amount
- Fee
- Tax
- Timestamp / settlement window
- Merchant reference
- Bank narration where appropriate

The deterministic engine will classify records into:

```text
MATCHED
PARTIALLY MATCHED
UNRESOLVED EXCEPTION
```

Only unresolved or ambiguous records are sent to the AI investigation layer.

This keeps the probabilistic reasoning layer focused on the cases where reasoning is actually useful.

---

## 8. Stage Two — ARGUS Agent Orchestration

The planned system uses an orchestrator that assigns unresolved finance exceptions to specialized agents.

The intention is not to create multiple agents simply for presentation value.

Each specialist exists because it has a distinct responsibility and toolset.

### 8.1 ARGUS Orchestrator

Responsibilities:

- Receive unresolved exceptions
- Build an investigation plan
- Decide which specialist is needed
- Coordinate agent execution
- Prevent conflicting actions
- Track investigation state
- Produce the final controller-level summary

### 8.2 Settlement Agent

Planned responsibilities:

- Reconstruct settlement composition
- Compare expected and actual settlement values
- Examine timing windows
- Analyse grouped payments
- Examine settlement identifiers and UTR mapping
- Identify whether a difference can be explained by legitimate settlement constituents

### 8.3 Refund Agent

Planned responsibilities:

- Identify full and partial refunds
- Detect duplicate merchant-side refund postings
- Identify refund timing mismatches
- Map refunds to payments and settlements
- Determine whether a refund explains an outstanding variance

### 8.4 Ledger Agent

Planned responsibilities:

- Compare bank / Razorpay-side events against merchant ledger records
- Detect possible duplicate postings
- Detect missing entries
- Detect incorrect debit/credit treatment
- Identify amount or reference inconsistencies
- Prepare evidence for a proposed journal correction

### 8.5 Exception Reasoning Agent

Handles difficult cases that cannot be resolved by one direct specialist rule.

Example investigation:

```text
Exception:
₹6,400 unexplained difference

Hypotheses:
1. Refund adjustment
2. Settlement timing difference
3. Fee or tax discrepancy
4. Duplicate ledger entry
5. Missing ledger transaction
6. Transaction belongs to another batch
```

The agent must use available tools and records to prove or disprove these hypotheses.

A hypothesis is not accepted merely because the language model considers it plausible.

### 8.6 Policy / Authority Agent

Checks whether a proposed action is allowed.

Example prototype policy:

```text
Auto-close evidence-backed informational exception:
Allowed

Prepare journal correction:
Allowed

Post correction under ₹5,000:
Allowed only if merchant policy permits

Correction above ₹5,000:
Human approval required

High-value exception above ₹50,000:
Mandatory escalation
```

The exact thresholds used in the Buildathon prototype will be defined explicitly in the synthetic merchant policy configuration.

They will not be presented as Razorpay policies.

### 8.7 Verification Layer

Before an exception is marked as resolved, the result must pass deterministic checks.

If the AI proposes:

> "The ₹6,400 difference is explained by refund RFND-102."

the verification layer must prove that:

- The refund exists
- The amount is correct
- It belongs to the correct payment
- It belongs to the relevant accounting / settlement context
- Applying the explanation removes the variance
- The same refund has not already been counted elsewhere

Only after successful verification can the system classify the exception as resolved.

---

## 9. Persistent Memory

ARGUS CONTROL is intended to have persistent merchant-specific memory.

Persistent memory will **not** be treated as the source of legal or financial truth.

### 9.1 Merchant operating memory

Examples:

- Preferred reporting style
- ERP/accounting convention
- Settlement accounting convention
- Finance approval limits
- Escalation preferences
- Previously approved operating preferences
- Recurring merchant-specific patterns

Example:

```text
Merchant preference:
Never auto-prepare corrections above ₹15,000
without finance-controller approval.
```

### 9.2 Operational memory

Examples:

- Previous finance-close runs
- Previously investigated exception patterns
- Repeated settlement timing patterns
- Previous human resolutions
- Unresolved cases carried forward
- Historical exception frequency

Operational memory can help the agent prioritize investigation hypotheses.

It does not automatically become policy.

### 9.3 Current-case memory

Contains:

- Records
- Tool results
- Active hypotheses
- Agent decisions
- Evidence
- Pending approvals
- Current exception state

### 9.4 Policies and laws are not conversational memory

Accounting policies, merchant SOPs, regulatory rules, and authoritative instructions must live in a **versioned policy knowledge store**.

Each stored policy should be identifiable by fields such as:

```text
Policy ID
Version
Effective date
Jurisdiction
Source
Status
Document hash
Relevant section
```

Memory tells ARGUS what happened before.

Policy tells ARGUS what it is allowed to do now.

These two concepts must remain separate.

---

## 10. Voice Interface

Voice is planned as the human supervision layer.

Voice is **not** the financial reasoning engine.

The finance controller should be able to give high-level instructions such as:

> "ARGUS, reconcile today's batch."

> "Show me the exceptions above ₹10,000."

> "Why could you not resolve exception 37?"

> "Show me the evidence."

> "Prepare corrections below ₹5,000 but do not post them."

> "Escalate everything with insufficient evidence."

The system translates those instructions into bounded tasks for the orchestrator.

Only one outward-facing voice controller is planned.

The internal specialist agents will communicate using structured data rather than having several voice agents talking to one another.

---

## 11. Bounded Autonomy

ARGUS CONTROL is not intended to have unrestricted financial authority.

### Safe autonomous actions

Potential examples:

- Run reconciliation
- Investigate exceptions
- Query records
- Classify exceptions
- Generate explanations
- Generate reports
- Prepare evidence
- Prepare proposed corrections
- Flag missing information
- Escalate cases

### Approval-gated actions

Potential examples:

- Posting accounting adjustments
- Closing high-value exceptions
- Changing ledger state
- Executing merchant-authorized corrections

For the Buildathon prototype, actions may be simulated where direct external financial write APIs are not available.

The UI must clearly distinguish:

```text
EXECUTED
PREPARED
WAITING FOR APPROVAL
ESCALATED
```

---

## 12. Honest Exception Handling

A central requirement is that ARGUS must be able to say:

> **"I cannot resolve this safely."**

An unresolved case is not considered a failure of the demo if the available evidence is genuinely insufficient.

The project will explicitly track:

- Resolved exceptions
- Incorrectly resolved exceptions
- Correctly escalated exceptions
- Incorrect escalations
- Cases lacking sufficient evidence

The Buildathon result will include an honest exception list.

---

## 13. Auditability

Every significant finance decision should be reproducible.

For each resolution, ARGUS is intended to record:

```text
Run ID
Exception ID
Records examined
Evidence used
Hypotheses tested
Agent/tool actions
Calculation performed
Policy applied
Persistent-memory context used
Confidence / uncertainty indicators
Verification result
Human approval if required
Final outcome
Timestamp
```

Example:

```text
Exception:
EXC-041

Finding:
Duplicate merchant-ledger refund posting

Evidence:
Refund RFND-8392 occurs once in payment/refund records
but twice in merchant ledger.

Proposed action:
Reverse duplicate journal entry.

Authority:
Human approval required because value > configured threshold.

Status:
WAITING_FOR_APPROVAL
```

---

## 14. Example End-to-End Scenario

A synthetic batch contains 500 financial records.

Deterministic reconciliation runs first.

Illustrative flow:

```text
500 records processed

438 matched deterministically
62 unresolved exceptions
```

One exception contains:

```text
Merchant Ledger Expected: ₹86,000
Observed Bank Credit:     ₹84,620
Difference:                ₹1,380
```

The exception agent investigates.

Possible hypothesis:

```text
Fee / tax treatment mismatch
```

Suppose the structured data proves:

```text
Gross Amount: ₹86,000
Fee:           ₹1,169
GST:             ₹211

Net:          ₹84,620
```

The deterministic verifier confirms:

```text
₹86,000 - ₹1,169 - ₹211 = ₹84,620
```

Result:

```text
Exception classification:
MERCHANT_LEDGER_GROSS_NET_TREATMENT

Resolution:
Explained successfully

Financial variance after explanation:
₹0

Status:
RESOLVED
```

No AI-generated amount is trusted without structured verification.

---

## 15. Example Difficult Scenario

Another exception:

```text
Ledger contains:
Refund RFND-901: -₹48,500
Refund RFND-901: -₹48,500

Razorpay-like refund data:
RFND-901: ₹48,500 COMPLETED
Occurrence count: 1
```

ARGUS investigates and proposes:

```text
Likely issue:
Duplicate merchant-ledger refund entry

Evidence:
1 refund in source transaction data
2 journal occurrences in merchant ledger

Proposed correction:
Reverse one duplicate ledger posting
```

If the amount exceeds the permitted autonomous authority:

```text
Status:
HUMAN APPROVAL REQUIRED
```

ARGUS prepares the correction but does not execute it.

---

## 16. Evaluation Plan

We will not claim performance metrics until the system has actually been run against the labelled dataset.

Planned metrics:

### Initial deterministic match rate

```text
Deterministically matched records
---------------------------------
Total eligible records
```

### Exception resolution rate

```text
Exceptions resolved by ARGUS
----------------------------
Total exceptions investigated
```

### Exception resolution accuracy

```text
Correct ARGUS resolutions
-------------------------
All ARGUS resolutions
```

### False-resolution rate

Measures cases where ARGUS closed an exception incorrectly.

### Human escalation rate

```text
Cases escalated
---------------
Total exceptions
```

### Correct escalation rate

Measures whether cases that genuinely lacked sufficient evidence were escalated rather than hallucinated into a resolution.

### Automated coverage

Percentage of the complete batch successfully handled without human intervention.

### Financial reconciliation accuracy

Whether final reconciled values match the labelled ground truth.

### Throughput

Potential measurements:

- Records processed per second
- Total batch-processing time
- Average exception-investigation time
- Median exception-investigation time

### Audit traceability

Percentage of resolved cases where the exact records and calculations supporting the outcome are available.

---

## 17. Benchmark Dataset

The repository is intended to include a synthetic benchmark dataset.

Planned exception categories include:

- Exact match
- Timing difference
- Partial refund
- Full refund
- Duplicate ledger posting
- Missing ledger posting
- Fee treatment difference
- GST treatment difference
- Settlement grouping mismatch
- Incorrect settlement mapping
- Incorrect transaction reference
- Transaction appearing in another settlement window
- Ambiguous bank narration
- Insufficient evidence
- High-value mandatory escalation
- Policy-bound approval case

The final dataset size will be at least the required 50+ records.

A larger batch will be used if implementation time permits.

---

## 18. What Makes ARGUS Different

ARGUS is not intended to compete by adding a chatbot to reconciliation.

Its intended differentiation is:

1. **Deterministic finance core** — easy financial matches use explicit rules and calculations.
2. **AI exception investigation** — agents focus only on unresolved cases.
3. **Multi-agent orchestration** — specialist responsibilities are coordinated by one controller.
4. **Persistent merchant memory** — operating context survives across runs.
5. **Versioned policy reasoning** — policies come from a controlled knowledge layer.
6. **Bounded autonomy** — agents operate only inside configured authority.
7. **Voice supervision** — the finance professional manages at controller level.
8. **Deterministic verification** — LLM plausibility is never enough to close an exception.
9. **Honest escalation** — unresolved cases remain unresolved when evidence is insufficient.
10. **Full audit trail** — conclusions remain traceable to records, calculations, policies, and actions.

---

## 19. Product Experience

The intended dashboard should feel like a finance operations control room rather than a traditional chatbot.

Example:

```text
ARGUS CONTROL
Finance Close · Batch AUG-20

--------------------------------------------------

ORCHESTRATOR
● Running

RECONCILIATION
✓ 438 records matched

SETTLEMENT AGENT
● Investigating 21 exceptions

REFUND AGENT
● Investigating 14 exceptions

LEDGER AGENT
✓ 18 exceptions resolved

POLICY ENGINE
◌ 6 cases waiting for approval

--------------------------------------------------

Records processed:       500
Matched:                 438
Exceptions:               62
Resolved by ARGUS:        --
Escalated:                --
Measured accuracy:        --
```

All final values shown in the actual product must come from the benchmark run.

No placeholder performance number will be presented as a measured result.

---

## 20. Intended Voice Demo

Possible Buildathon interaction:

### Finance Controller

> "ARGUS, close today's batch and tell me what actually needs my attention."

ARGUS runs reconciliation and exception investigation.

### Finance Controller

> "Show me the highest-value unresolved exception."

ARGUS retrieves the relevant case.

### Finance Controller

> "Why could you not resolve it?"

ARGUS shows:

- Financial records
- Evidence
- Hypotheses tested
- Policy
- Missing information

### Finance Controller

> "Prepare corrections for all verified exceptions below my approval threshold. Do not post anything above it."

ARGUS prepares only the permitted actions.

The demo then shows the complete audit trail.

---

## 21. Five-Minute Pitch Vision

### 0:00–0:40 — Problem

Show a finance team facing a large reconciliation batch.

Explain that straightforward records can be matched mechanically, but exceptions create manual investigative work.

### 0:40–1:10 — ARGUS CONTROL

Introduce:

```text
Deterministic matching
+
AI exception investigation
+
Policy-aware bounded actions
+
Persistent memory
+
Voice supervision
```

### 1:10–3:40 — Live Demo

- Load a 50+ record synthetic batch
- Run reconciliation
- Show measured match rate
- Show agent orchestration
- Investigate at least one difficult exception
- Demonstrate voice supervision
- Demonstrate an approval boundary
- Show one unresolved exception where ARGUS refuses to guess

### 3:40–4:30 — Benchmark

Show:

- Match rate
- Resolution accuracy
- False-resolution rate
- Throughput
- Escalation rate
- Honest unresolved exception list

### 4:30–5:00 — Architecture and Vision

Show:

- Finance engine
- Agent orchestration
- Persistent memory
- Policy store
- Verification
- Auditability

End with the long-term product vision:

> A Razorpay-native AI Finance Controller that lets merchant finance teams supervise financial operations rather than manually inspect every transaction.

---

## 22. Planned Repository Structure

```text
argus-control/
│
├── README.md
├── ARCHITECTURE.md
├── EVALUATION.md
├── BUILD_CHALLENGES.md
│
├── apps/
│   ├── web/
│   └── api/
│
├── services/
│   ├── reconciliation/
│   ├── orchestrator/
│   ├── settlement-agent/
│   ├── refund-agent/
│   ├── ledger-agent/
│   ├── exception-agent/
│   ├── policy-engine/
│   ├── memory/
│   ├── verification/
│   └── voice/
│
├── packages/
│   ├── financial-types/
│   ├── agent-tools/
│   └── audit/
│
├── data/
│   ├── synthetic/
│   ├── ground-truth/
│   └── policies/
│
├── evaluation/
│   ├── benchmark/
│   └── results/
│
├── tests/
│
├── .env.example
└── docker-compose.yml
```

This is the intended architecture, not a claim that every component already exists.

---

## 23. Technical Decisions Still To Be Finalized

The project vision and architecture are defined.

The exact implementation stack is **not yet frozen**.

Before development, we still need to finalize:

- Web framework
- Backend framework
- Agent orchestration framework
- LLM provider/model
- Voice provider
- Database
- Retrieval layer if required
- Persistent-memory implementation
- Exact Razorpay sandbox/tool integrations available for the prototype
- Exact accounting data format
- Exact benchmark size
- Deployment platform

These choices will be made based on Buildathon constraints, available APIs, implementation time, reliability, and reproducibility.

They should not be treated as decided until they are committed to the repository.

---

## 24. Build Challenges We Intend to Solve

### Hybrid deterministic + agentic architecture
Keeping arithmetic and record matching deterministic while using LLM agents only where reasoning adds value.

### Financial hallucination prevention
Ensuring that a plausible AI explanation cannot close an exception unless structured evidence verifies it.

### Agent coordination
Preventing multiple agents from producing contradictory conclusions or duplicate actions.

### Persistent memory boundaries
Remembering merchant context without allowing historical conversational memory to override current policy.

### Policy precedence
Applying merchant preferences, internal policies, and approval boundaries predictably.

### Reproducible evaluation
Building a labelled synthetic dataset with enough diversity to objectively measure the system.

### Honest uncertainty
Escalating cases when the evidence is insufficient instead of forcing a resolution.

### Voice-to-action safety
Translating natural-language instructions into structured, permission-checked financial operations.

### Auditability
Recording enough information to reconstruct why every action or decision occurred.

These are intended engineering challenges.

The final submission should describe only the challenges that were actually encountered during implementation.

---

## 25. Safety Philosophy

ARGUS CONTROL will follow five core rules.

1. **LLMs do not perform final financial arithmetic.**
2. **No exception is resolved without structured evidence.**
3. **No financial action exceeds the merchant-defined authority boundary.**
4. **Insufficient evidence results in escalation, not invention.**
5. **Every material decision is auditable.**

---

## 26. What We Are Not Claiming

At the planning stage, we are explicitly **not** claiming:

- Access to Razorpay production systems
- Access to Razorpay private internal APIs
- Access to real merchant financial data
- Production deployment inside Razorpay
- Measured reconciliation accuracy
- Measured agent accuracy
- Measured throughput
- Real money recovered
- Real financial corrections executed
- Regulatory compliance certification
- Accounting certification
- Support for every ERP/accounting platform
- Fully autonomous production money movement

Any performance number included later must come from the actual benchmark implementation.

---

## 27. Buildathon Success Criteria

The project will be considered successfully implemented when it can demonstrate the following end to end:

1. Load a batch containing at least 50 synthetic financial records.
2. Normalize the input data.
3. Deterministically reconcile straightforward records.
4. Calculate and display the actual initial match rate.
5. Create an honest unresolved exception list.
6. Send unresolved exceptions into the agent investigation layer.
7. Allow agents to investigate using structured tools and evidence.
8. Verify proposed explanations deterministically.
9. Correctly classify cases as resolved, approval-required, or unresolved.
10. Respect merchant-defined authority policies.
11. Persist merchant operating context across sessions.
12. Allow finance-controller supervision through voice or UI.
13. Maintain an auditable history of agent actions and decisions.
14. Run the same system against labelled ground truth.
15. Report actual measured accuracy and throughput.
16. Demonstrate at least one case that ARGUS intentionally refuses to resolve because the evidence is insufficient.

---

## 28. Long-Term Product Vision

If a system like ARGUS CONTROL were ever integrated natively into Razorpay, the intended relationship would be:

```text
RAZORPAY
Payments
Refunds
Settlements
Fees
Taxes
Disputes
Payouts
        ↓
ARGUS CONTROL
Autonomous Finance Operations Layer
        ↓
MERCHANT FINANCE TEAM
```

A Razorpay-native version could potentially have richer authorized access to payment and settlement context than an external reconciliation tool.

The merchant could connect its accounting and banking systems and supervise the finance-close process through one controller.

This is a future product vision only.

It is not a claim about the Buildathon prototype.

---

## 29. One-Line Project Definition

> **ARGUS CONTROL is a voice-supervised AI Finance Controller that deterministically reconciles multi-source financial data, autonomously investigates the exceptions that remain, reasons with persistent merchant context and versioned policies, verifies every proposed resolution against structured evidence, and escalates only the cases it cannot safely resolve.**

---

## 30. Buildathon Vision

The goal of this project is not to build another finance chatbot.

The goal is to demonstrate a finance agent that can be given a real operational objective:

> **"Close this batch."**

ARGUS should then:

```text
Understand the job
        ↓
Run deterministic reconciliation
        ↓
Identify exceptions
        ↓
Investigate difficult cases
        ↓
Use policies and merchant context
        ↓
Test financial hypotheses
        ↓
Verify every conclusion
        ↓
Act only within delegated authority
        ↓
Escalate ambiguity
        ↓
Report measurable results
        ↓
Preserve a complete audit trail
```

The intended experience is that the finance controller supervises the operation instead of manually working every row.

That is the vision for **ARGUS CONTROL in Razorpay Buildathon Track 04**.
