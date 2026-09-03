# September 4 import reliability checkpoint

This closes the scoped import-reliability workstream. It is not a new PRD phase
certification, a production deployment certificate, or evidence of a live
Razorpay import. All verification used isolated fictional data and no credentials.

## What changed

### Atomic gateway activation

The three gateway sources (`payments`, `refunds`, and `settlements`) now activate
as one bundle. ARGUS first validates every generated CSV and writes every raw and
canonical immutable object. Only after all writes succeed does one atomic manifest
replacement select the three revisions. A failed preparation cannot expose one or
two new sources alongside an older third source.

Original upload names remain immutable metadata, but they are no longer embedded
in deep storage paths. Content-addressed `.s/src-*.raw` and `.s/src-*.csv` names
remove the reproduced Windows `MAX_PATH` failure without discarding provenance.
Temporary writes are flushed and renamed; failed retries cannot mistake a partial
file for a completed immutable object.

Session changes are guarded by both an in-process reentrant lock and an operating-
system file lock. Different backend processes therefore cannot overwrite each
other's manifest updates.

### Recoverable filesystem/SQLite boundary

SQLite and the filesystem cannot share a real transaction. ARGUS now uses an
explicit durable-receipt pattern instead of pretending they can:

1. The atomic manifest switch includes a checksummed activation receipt.
2. The receipt is projected into gateway status, demo history, and append-only
   audit in one immediate SQLite transaction.
3. The receipt ID is the audit event ID, making repeated delivery idempotent.
4. Session reads and mutations replay any receipt not yet present in the audit log.

A failure before the manifest switch leaves the previous bundle active. A crash
after the switch leaves the complete new bundle active and its receipt pending.
The API returns `503 ACTIVATION_RECOVERY_PENDING`, and reopening the session
replays the projection. Recovery records history; it never reselects revisions,
so a later manual replacement is not undone.

### Immutable reconciliation inputs

Readiness is derived from one locked, hash-verified manifest. When ready, ARGUS
copies the selected canonical revisions into an immutable `.runs/<evidence-hash>`
snapshot and runs reconciliation from that directory. An upload that arrives
afterward can form a future run but cannot alter an in-flight run. Repeating the
same completed reconciliation reuses its run instead of replacing its cases,
proofs, approvals, or audit trail.

### Bounded Razorpay reads

Razorpay reads remain request-scoped and read-only. Each page has a ten-second
operation timeout, transient `429`/`5xx` and transport failures have at most three
attempts, and the client shares a 90-second budget across the entire five-feed
import. `Retry-After` is respected when it fits the remaining budget. The first
failed feed stops the sequence and no partial API response is staged.

Responses that are malformed, contain non-object rows, exceed the safe page size,
or exceed the selected record cap fail explicitly. A full final page triggers a
one-row look-ahead, so a 601-row period cannot be silently reported as a complete
600-row import. Settlement-reconciliation filtering occurs before that cap is
evaluated.

The pinned Next.js runtime's rewrite proxy is bounded at 120 seconds, leaving room
for the backend's 90-second read budget and local atomic staging. This addresses
the reproduced default 30-second socket reset without introducing an unbounded
request.

## Verification evidence

On September 4, 2026, the final source state passed:

- 510 backend tests, including 34 new reliability/fault tests; one existing
  third-party TestClient deprecation warning remains.
- Backend Ruff check and format check across 152 files.
- Backend mypy across 92 source files.
- Frontend ESLint, strict TypeScript, and 41 Vitest tests.
- An isolated Next.js production build targeting an isolated backend on port
  8017; its server config contains `proxyTimeout: 120000`.
- 19 Playwright Chromium tests against an isolated SQLite database and staging
  tree, including the gateway-only and merchant-upload lifecycle.
- Label firewall, repository secret scan, and gitignore coverage checks.

Fault coverage includes failures at each of the six immutable writes, manifest
replacement, SQLite/audit projection, a real child-process exit after the atomic
switch, repeated recovery, manual supersession after pending recovery, cross-
process lock exclusion, immutable run snapshots, idempotent run retry, transient
and permanent HTTP errors, transport failure, shared deadline exhaustion,
malformed payloads, exact record limits, later-page failure, and date filtering.

## What this does not claim

- No real Razorpay credentials or provider endpoint were exercised by this test
  run. The owner should perform the short manual Test Mode check below.
- Credentials deliberately cannot survive a process crash; a fetch interrupted
  before staging requires the user to submit them again.
- An external deployment proxy may have its own timeout and is a deployment-step
  concern. ARGUS itself now has explicit bounded semantics.
- Durable asynchronous job orchestration, live Groq investigation, the 500–600
  captured-payment scenario, dossier/export refinement, and production deployment
  certification remain later approved workstreams.

## Owner acceptance check

Restart the backend so it loads this code, then:

1. Open **Import Data** and import a known Razorpay Test Mode period.
2. Confirm orders/payments/refunds and the import ID appear. If the selected
   period reaches its cap, expect an explicit instruction to narrow the range,
   not a truncated success.
3. Generate labelled gateway evidence. Confirm only the three gateway revisions
   change and bank/ledger still require separate uploads.
4. Hard-refresh and reopen Import Data. Confirm the same import/evidence restores.
5. Upload the companion bank and merchant-ledger CSVs, reconcile twice, and
   confirm the second response has the same run ID with `reused: true`.

Do not clear the database between these checks. Existing sessions and immutable
source history are intentionally compatible with the new storage layout.
