# ARGUS CONTROL Verification Rules

Phase 3 adds deterministic verification, proof packages, and dry-run correction
previews. It does not add AI investigation, approval, correction application,
dashboard metrics, voice, or multilingual behavior.

## Rule Manifest

Verifier manifest: `verify-rules-v1`.

- `V-DUPLICATE-LEDGER` version `1`
- `V-MISSING-REFUND` version `1`
- `V-TIMING-WINDOW` version `1`
- `V-AMBIGUITY` version `1`

Synthetic merchant policy constants:

- refund posting window: 3 days
- timing adjacency: 3 days
- maximum composition refunds: 16

These values are demo policy, not Razorpay policy.

## Global Checks

Every case is checked before category logic:

- hypothesis category and case category must match;
- every cited evidence id must exist in the evidence snapshot;
- every cited record must match the case currency;
- exclusive evidence already claimed by a prior PASS proof is rejected;
- required evidence shape must be present.

Free-form claim text is never parsed for arithmetic.

## Category Outcomes

`DUPLICATE_LEDGER_POSTING` passes only when duplicate ledger rows cite one
source-side event, share amount/account/currency, and the derived delta closes
the case variance. Nonzero deltas become `APPROVAL_REQUIRED`.

`MISSING_REFUND_POSTING` passes only when a processed refund belongs to a valid
payment and no direct or uniquely-composed ledger posting covers it inside the
posting window. Nonzero deltas become `APPROVAL_REQUIRED`.

`SETTLEMENT_TIMING_WINDOW_SHIFT` passes only when the settlement booking is
outside its own window but within the allowed adjacency band, the amount is
identical, and the candidate is unique. Its delta is always zero and the case
becomes `VERIFIED_RESOLVED`.

`AMBIGUOUS_EVIDENCE` cannot pass in Phase 3. It always returns
`INCONCLUSIVE`, records the missing discriminator, and leaves the case
`UNRESOLVED`.

## Proof Packages

Every verification produces a proof package with:

- case and hypothesis ids;
- cited, supported, and conflicting evidence ids;
- rule id and version;
- equations with concrete integer-paise expressions;
- rejected alternatives where applicable;
- reconciliation and verifier manifest fingerprints;
- proposed delta only on PASS;
- authority decision;
- canonical SHA-256 hash.

Proof ids are content-addressed from canonical JSON. Creation time is stored
but excluded from the canonical hash, so identical inputs produce identical
proof ids.

## Dry-Run Boundary

`preview_correction` is a pure function. It can produce a DRAFT correction
preview for a PASS proof, but it never writes financial tables and never
constructs a persisted `SIMULATED_CORRECTION` ledger entry.

Phase 3 may persist hypotheses, proofs, and DRAFT correction previews as run
outputs. These rows are proof artifacts, not applied corrections.

Dry-run previews independently re-derive the delta from evidence and refuse
non-PASS proofs or deltas that do not close nonzero variance.

## Authority Policy

Synthetic merchant policy `authority-policy-v1` maps verifier outcomes:

- PASS with zero delta: `VERIFIED_RESOLVED`
- PASS with nonzero delta: `APPROVAL_REQUIRED`
- FAIL: `VERIFICATION_FAILED`
- INCONCLUSIVE: `UNRESOLVED`

There is no confidence input and no approval/apply path in Phase 3.
