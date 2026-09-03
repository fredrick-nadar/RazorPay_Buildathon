# Chunk 1 — Trustworthy Import Cornerstone

Date: 2026-09-01

Status: PASS (implementation and local verification; owner acceptance pending)

## Scope decisions applied

1. Source replacements are immutable revisions. The most recently confirmed
   revision becomes active; earlier raw and canonical revisions remain intact.
2. Razorpay Test Mode credentials are entered in the dashboard, used only for
   that request, cleared from the form, and never persisted by the import path.
3. Razorpay-side evidence is API-only in the dashboard. One request-scoped
   credential pair retrieves orders, captured payments, refunds, settlement
   summaries, and combined settlement-reconciliation rows for a user-selected
   date range. The user does not maintain Razorpay CSV exports.
4. CSV is the only manual format in this cornerstone. OCR, image, PDF, and XLSX
   import paths remain disabled.

## Implemented flow

1. The browser creates one tab-scoped import session identifier and preserves it
   across refreshes using session storage.
2. A Razorpay import fetches orders, payments, refunds, settlements, and the
   official combined settlement-reconciliation feed using request-scoped Test
   Mode credentials. The selected interval is UTC-bounded, audited, limited to
   366 days, and paginated with an explicit per-resource record cap.
3. All returned gateway entities are captured immutably in SQLite. The snapshot
   initially has status `CAPTURED`.
4. Reconciliation-eligible gateway rows are rendered into canonical CSV without
   inventing missing values. Settlement reconciliation supplies settlement IDs,
   currency, event windows, row-level fee/tax, and UTR evidence that the basic
   settlement listing does not provide. Every canonical row is then validated
   deterministically; incomplete evidence is quarantined.
5. Raw API payloads and canonical CSVs are content-addressed as immutable source
   revisions. Successful activation changes the gateway import status to
   `STAGED` and emits an append-only audit event.
6. Manual CSVs are profiled, mapped using deterministic aliases and optionally
   bounded Groq header proposals, reviewed by the user, canonicalized, and
   deterministically validated. Groq cannot rewrite cell values.
7. Backend session status is the source of truth for readiness. Restarting the
   API or refreshing the page does not discard active-source state.
8. Gateway readiness requires at least one eligible payment and settlement.
   Bank and merchant-ledger readiness are evaluated independently.
9. A reconciliation run is refused until gateway, bank, and ledger evidence are
   all ready. Missing evidence is not converted into a fake exception.
10. Only the reconciliation run creates dossier cases. A captured or staged
    import alone does not create a case dossier.
11. The only user-managed uploads shown in the interface are a bank-statement
    CSV and a merchant-ledger CSV.

## Safety properties verified

- Raw and canonical revisions are immutable and hash checked.
- Re-uploading identical content is idempotent; no duplicate revision is made.
- A new source replaces only the active pointer, not revision history.
- Manifest corruption and path escape attempts fail closed.
- Accepted plus quarantined rows must equal the canonical row count.
- Razorpay Key Secret is represented as `SecretStr` at the API boundary and is
  absent from snapshot rows, the import manifest, audit payloads, and responses.
- The repository secret scan now detects Groq `gsk_` keys and correctly checks
  ignored local `.env` files before extension filtering.
- Runtime label isolation remains healthy.

## Verification evidence

- Backend full suite: 438 passed.
- Focused import/gateway suite: 22 passed.
- Ruff check: passed.
- Ruff format check: passed.
- mypy: passed for 89 application source files.
- Frontend ESLint: passed.
- Frontend TypeScript check: passed.
- Frontend Vitest: 2 passed.
- Frontend production build: passed.
- Repository secret scan: passed.
- Runtime label firewall: passed.
- `git diff --check`: passed (line-ending notices only).
- Browser smoke test: dashboard loaded, import dialog opened, and the rendered
  schema showed both Test Mode credential fields, a start/end date range, all
  five official Razorpay feeds, and only bank/ledger CSV upload controls. The
  pre-existing development server logged stale React Server Component prefetch
  warnings after the production build rewrote `.next`; the dialog itself
  rendered and remained interactive.

The backend tests emitted the pre-existing Starlette/httpx deprecation warning
and a pytest cache-permission warning. Neither affected test results. One repeat
focused test command initially used the same temporary directory as stopped
development-server logs and could not clean those locked log files; the command
was rerun in a fresh isolated directory and all 21 tests passed.

## Owner acceptance tests

Use synthetic/Test Mode data only.

1. Razorpay credentials with captured payments and settlement reconciliation in
   the selected period: expect gateway ready, but no dossier until bank and
   ledger CSVs are active.
2. Razorpay credentials with captured payments but no processed settlement
   reconciliation in the period: expect an amber date-range instruction and
   gateway not ready. No Razorpay upload fallback is displayed.
3. Invalid or incomplete credentials: expect the whole gateway import to fail
   closed with no partial snapshot presented as ready.
4. Upload a bank CSV with familiar headers and one with unusual headers: review
   deterministic/Groq proposals and confirm that source values remain unchanged.
5. Upload a second bank CSV: expect revision 2 active while revision 1 remains
   preserved.
6. Upload a malformed row: expect it to be counted and quarantined, never
   silently dropped.
7. Refresh the dashboard and reopen Import Data: expect the same session suffix,
   active revisions, and readiness state.
8. Complete all three evidence groups and run reconciliation: expect a run ID;
   only then inspect the resulting cases in Case Dossier.
