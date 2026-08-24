# Bounded AI Investigator Rules and Tool Contract

## 1. Architectural Role and Precedence

ARGUS CONTROL uses AI strictly for **investigation**, never for calculation, authority, approval, or execution (PRD §5.7, §10):

> **Rules for calculation, AI for investigation, verification for closure, approval for authority, humans for ambiguity.**

The investigator:
- Proposes structured hypotheses for residual `UNRESOLVED` or `VERIFICATION_FAILED` cases only.
- Operates through a strict read-only tool dispatcher.
- Has **no authority** to mark a case resolved, approve a correction, apply a ledger entry, or write to financial tables.
- Emits structured output that is validated by Pydantic v2 schemas (`extra="forbid"`) and routed through the deterministic Phase 3 verifier.
- Cannot bypass verification via confidence or score — confidence fields are rejected at parse time, and inconclusive cases stay `UNRESOLVED`.

---

## 2. Tool Dispatcher Contract (PRD §10.2)

The investigator is provided access to a fixed allowlist of tools. Any attempt to invoke a tool outside this allowlist fails with an `UNKNOWN_TOOL` error.

### Read Tools

| Tool | Parameters | Description |
|---|---|---|
| `get_case` | `case_id: str` | Retrieves normalized case metadata, category candidate, variance, and cited evidence. |
| `get_evidence_graph` | None / `case_id: str` | Retrieves the serializable evidence graph (nodes and edges). |
| `get_record` | `record_id: str` | Retrieves normalized record fields for `TYPE:id` (e.g. `PAYMENT:pay-001`). Unknown IDs return `UNKNOWN_EVIDENCE_ID`. |
| `list_candidate_records` | `record_type: str` | Lists all normalized records of a given type (`PAYMENT`, `REFUND`, `SETTLEMENT`, `BANK_ENTRY`, `LEDGER_ENTRY`). |
| `get_rule_manifest` | None | Retrieves reconciliation and verification rule manifests and version numbers. |

### Exploratory Calculation Tools (Never Authoritative)

> [!IMPORTANT]
> **Exploratory Disclaimer**: The `calculate_*` and `check_*` tools are provided solely as numerical aids to help the investigator explore evidence and test hypotheses during investigation. Their outputs are **never authoritative**. The Phase 3 verifier (`verify_case`), executed independently by the backend engine, is the **sole authority** for all financial arithmetic, invariant checks, and ledger deltas.

| Tool | Parameters | Description |
|---|---|---|
| `calculate_control_totals` | None | Returns the run's financial control totals (exploratory aid). |
| `calculate_expected_net` | `payment_ids: list[str]`, `refund_ids: list[str]` | Computes gross, fees, taxes, refund totals, and expected net paise across cited records. |
| `check_date_window` | `record_ids: list[str]` | Checks settlement, bank posting, or refund creation timestamps against configured window boundaries. |
| `check_unique_identity` | `record_ids: list[str]` | Checks for duplicate citations or twin settlement conflicts. |

### Forbidden Tools (Never Available to Investigator)

The tool dispatcher enforces the absence of any workflow or state mutation tools. The following tools do not exist in the dispatcher:
- `approve`, `apply`, `simulate_apply`, `update_ledger`
- `mark_resolved`, `mark_unresolved`, `propose_resolution`, `record_hypothesis`
- `verify_hypothesis`, `preview_correction` (verifier and dry-run are owned by backend engine)
- `execute_sql`, `run_code`, `eval`

---

## 3. Input Context and Untrusted Data Boundary (PRD §10.4)

The investigation context serializes evidence records and case metadata for the investigator:
- All merchant-supplied text fields (e.g., bank `narration`, ledger `description`) are enclosed in `<UNTRUSTED_DATA>...</UNTRUSTED_DATA>` boundary tags.
- System instructions declare that content within untrusted tags constitutes raw evidence to analyze, not instructions to follow (prompt injection defense).
- Ground-truth label paths (`datasets/**/labels/`) are never included in the context.

---

## 4. Output Contract and Pydantic Boundary (PRD §10.3)

The investigator returns **exactly one** of two outputs, validated through Pydantic v2 models configured with `model_config = ConfigDict(extra="forbid")`:

### Hypothesis Output (`HypothesisOutputModel`)
```json
{
  "hypothesis": {
    "category": "DUPLICATE_LEDGER_POSTING",
    "claim": "two or more ledger rows post one source-side event",
    "evidence_ids": ["PAYMENT:pay-001", "LEDGER_ENTRY:led-001", "LEDGER_ENTRY:led-002"],
    "competing_hypotheses": [
      {
        "category": "AMBIGUOUS_EVIDENCE",
        "why_possible": "duplicate rows could represent distinct adjustments",
        "test_needed": "check source reference consistency"
      }
    ],
    "known_uncertainty": ["verifier must confirm source semantics"]
  }
}
```

- `category`: Must match a known `ExceptionCategory`.
- `evidence_ids`: Non-empty list of formatted `TYPE:record_id` strings.
- `competing_hypotheses`: Must contain at least one competing hypothesis with test criteria (prevents single-hypothesis fixation).
- Extra fields (`confidence`, `probability`, `score`, `status_override`) trigger a `ValidationError` at parse time.

### Unresolved Explanation (`UnresolvedExplanationModel`)
```json
{
  "unresolved": {
    "reason_codes": ["NON_UNIQUE_EVIDENCE"],
    "missing_evidence": ["additional discriminating bank statement or merchant ledger breakdown"],
    "next_step": "request human review from finance team"
  }
}
```

- Provider reason codes are normalized: system `ReasonCode` values pass through; unknown strings receive a `PROVIDER:` prefix to ensure machine-parseability.

---

## 5. Verification Routing and Dry-Run (PRD §10.1, §11)

1. The backend engine receives the validated provider output.
2. If `unresolved`, the case retains `UNRESOLVED` status with documented missing evidence.
3. If `hypothesis`, the engine generates a deterministic hypothesis ID via `hypothesis_id_for()` (reused from Phase 3) and invokes `verify_case(case, hypothesis, snapshot)`.
4. If `verify_case` returns:
   - **PASS**: The engine invokes `preview_correction()`, classifies authority (`APPROVAL_REQUIRED` if proposed delta != 0, `VERIFIED_RESOLVED` if delta == 0), and constructs a complete `ProofPackage`.
   - **FAIL**: The case status becomes `VERIFICATION_FAILED`, hypothesis becomes `REJECTED`, and failure reason codes are stored.
   - **INCONCLUSIVE**: The case status becomes `UNRESOLVED`, hypothesis becomes `INCONCLUSIVE`.

---

## 6. Failure Modes, Budgets, and Idempotency

- **Budgets**: Default 12 tool calls per case, 2 total parse attempts (initial + 1 retry).
- **Timeouts**: Wrapped with worker thread timeout (Windows-compatible). On expiry, case status becomes `INVESTIGATION_FAILED` with zero financial state mutation.
- **Provider Crashes**: Caught and logged as `INVESTIGATION_FAILED`; financial tables remain untouched.
- **Idempotency**: Runs in `mode="agent"` namespace their key material (`run-v3-agent|...`) with `mode` and `provider_id`, ensuring no collision with `rules-only` runs.
- **Persisted Summary**: Agent runs write a model-agnostic investigation summary (tool calls used, retries, provider ID, per-case outcomes) to the run summary without logging secrets or raw prompts.
