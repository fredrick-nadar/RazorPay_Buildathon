/**
 * Chunk 3C: the complete finance-loop browser acceptance gate.
 *
 * Runs against the isolated synthetic database the Playwright global setup
 * seeds and serves. No live Razorpay, Groq, Sarvam or other provider is
 * contacted: the backend is started with every provider key blanked and
 * `ARGUS_AI_PROVIDER=none`, and the only stubbed responses here are explicit
 * `page.route` interceptions used to reproduce transport-level conditions the
 * isolated backend cannot be made to produce on demand (an outage, a slow
 * response arriving out of order). Each of those is labelled where it is used,
 * and the corresponding backend behaviour is proved separately by
 * `backend/tests/unit/test_chunk3c_cross_view_truth.py`.
 *
 * The seeded database holds two persisted runs:
 *   clean_run_id      15/15 records matched, zero exception cases
 *   exception_run_id  282 records, 12 cases across the four mandatory classes
 *
 * Both are addressable through the URL selection, which is what makes the
 * zero-exception and exception scenarios reachable in one browser session.
 */

import { expect, test, type Page, type Route } from "@playwright/test";
import { runs, scenario, withImport } from "./fixture";

const { clean_run_id: CLEAN_RUN, exception_run_id: EXCEPTION_RUN } = runs();

const IMPORT_SESSION_KEY = "argus_import_session_v1";

// Every browser request must stay local. An external host is aborted, so this
// suite cannot reach Razorpay, Groq, Sarvam or any other provider even if a
// component were to try. Fault injection below is explicit and local.
test.beforeEach(async ({ page }) => {
  await page.route("**/*", (route: Route) => {
    const url = new URL(route.request().url());
    return ["127.0.0.1", "localhost"].includes(url.hostname)
      ? route.continue()
      : route.abort();
  });
});

/** Load the dashboard as if this browser had been using `sessionId` all along. */
async function openImportDialog(page: Page, sessionId: string): Promise<void> {
  await page.addInitScript(
    (seed: { key: string; value: string }) => {
      window.sessionStorage.setItem(seed.key, seed.value);
    },
    { key: IMPORT_SESSION_KEY, value: sessionId },
  );
  await page.goto("/dashboard");
  await page.getByRole("button", { name: /import data/i }).click();
  await expect(page.getByRole("heading", { name: "Razorpay Test Mode" })).toBeVisible();
}

/** A case in a given status, read straight from the isolated backend. */
async function caseInStatus(page: Page, runId: string, status: string) {
  const response = await page.request.get(
    `/api/v1/runs/${runId}/cases?status=${encodeURIComponent(status)}`,
  );
  expect(response.ok()).toBeTruthy();
  const cases = (await response.json()) as Array<{ case_id: string; run_id: string }>;
  const first = cases[0];
  if (first === undefined) throw new Error(`fixture has no ${status} case in ${runId}`);
  expect(first.run_id).toBe(runId);
  return first;
}

async function caseDetail(page: Page, runId: string, caseId: string) {
  const response = await page.request.get(`/api/v1/cases/${caseId}?run_id=${runId}`);
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as {
    case: { case_id: string; run_id: string; status: string };
    proof: { proof_id: string; verifier_status: string } | null;
    dry_run: {
      proposed_delta_paise: number;
      variance_before_paise: number;
      variance_after_paise: number;
    } | null;
    simulated_correction: { correction_id: string; delta_paise: number } | null;
  };
}

type CaseDossier = Awaited<ReturnType<typeof caseDetail>>;

/** A held promise, for reproducing an out-of-order response arrival. */
function deferred(): { promise: Promise<void>; release: () => void } {
  let release: () => void = () => undefined;
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release: () => release() };
}

function requiredProof(detail: CaseDossier): NonNullable<CaseDossier["proof"]> {
  if (detail.proof === null) throw new Error(`${detail.case.case_id} has no proof`);
  return detail.proof;
}

function requiredDryRun(detail: CaseDossier): NonNullable<CaseDossier["dry_run"]> {
  if (detail.dry_run === null) throw new Error(`${detail.case.case_id} has no dry-run`);
  return detail.dry_run;
}

function requiredApplied(
  detail: CaseDossier,
): NonNullable<CaseDossier["simulated_correction"]> {
  if (detail.simulated_correction === null) {
    throw new Error(`${detail.case.case_id} has no simulated correction`);
  }
  return detail.simulated_correction;
}

function dashboardUrl(params: Record<string, string>): string {
  return `/dashboard?${new URLSearchParams(params).toString()}`;
}

/* ------------------------------------------------------------------ */
/* 1. Empty database / new user                                        */
/* ------------------------------------------------------------------ */

test.describe("empty database", () => {
  test("a new user is offered one truthful path to a first run", async ({ page }) => {
    // Transport stub: the isolated database is seeded, so the empty contract is
    // reproduced here. That the backend really answers `null` on a fresh
    // database is proved by test_active_run_is_null_on_an_empty_database.
    await page.route("**/api/v1/runs/active", (route: Route) =>
      route.fulfill({ status: 200, json: null }),
    );
    await page.goto("/dashboard");

    await expect(page.getByText("No reconciliation run yet", { exact: true })).toBeVisible();
    // Exactly one primary action on screen, not the same button twice.
    await expect(page.getByRole("button", { name: "Import evidence" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Import evidence" })).toHaveCount(1);
    await expect(page.getByText("Nothing to answer from yet")).toBeVisible();
    // Backend is reachable and simply holds nothing; that is not an error.
    await expect(page.getByText("Backend reachable", { exact: true })).toBeVisible();
    await expect(page.getByText("Backend unavailable")).toHaveCount(0);
    // No run identity is claimed, and no metric is invented.
    await expect(page.getByTestId("active-run-identity")).toHaveCount(0);
    await expect(page.getByTestId("home-run-empty")).toBeVisible();
    await expect(page.getByTestId("home-run-facts")).toHaveCount(0);
  });

  test("an empty database still explains itself in presentation mode", async ({ page }) => {
    await page.route("**/api/v1/runs/active", (route: Route) =>
      route.fulfill({ status: 200, json: null }),
    );
    await page.goto("/presentation");

    await expect(page.getByRole("heading", { name: /nothing has been reconciled/i })).toBeVisible();
    await expect(page.getByText("Backend reachable", { exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: /open the control room/i })).toBeVisible();
  });

  test("a missing presentation run stays distinct from an outage", async ({ page }) => {
    await page.goto("/presentation?run=run-does-not-exist");

    await expect(page.getByRole("heading", { name: /that run no longer exists/i })).toBeVisible();
    await expect(page.getByText("Backend reachable · run not found", { exact: true })).toBeVisible();
    await expect(page.getByText("Backend unavailable", { exact: true })).toHaveCount(0);

    await page.getByRole("button", { name: /show the active run instead/i }).click();
    await expect(page).toHaveURL(/\/presentation$/);
    await expect(page.getByText("Active batch", { exact: true })).toBeVisible();
  });
});

/* ------------------------------------------------------------------ */
/* 2. Pending gateway settlement evidence                              */
/* ------------------------------------------------------------------ */

test("pending gateway evidence explains what is still missing", async ({ page }) => {
  const pending = withImport("pending");
  await openImportDialog(page, pending.session_id);

  // Captured payments exist, but no settlement has arrived. The intake states
  // that and keeps reconciliation unavailable, rather than presenting the
  // session as ready or silently reconciling an incomplete chain.
  await expect(page.getByText(/awaiting settlement evidence/i)).toBeVisible();
  await expect(page.getByText(`Import ${pending.import_id}`, { exact: true })).toBeVisible();

  // The dashboard behind the dialog still shows the persisted run, so a
  // pending intake never blanks the rest of the control room.
  await page.getByRole("button", { name: "Close" }).click().catch(() => undefined);
  await expect(page.getByTestId("active-run-identity")).toContainText(EXCEPTION_RUN);
});

/* ------------------------------------------------------------------ */
/* 3. Clean reconciliation with a legitimate zero-case result          */
/* ------------------------------------------------------------------ */

test.describe("clean reconciliation", () => {
  test("a zero-exception run reads as success, not as broken", async ({ page }) => {
    await page.goto(dashboardUrl({ run: CLEAN_RUN }));

    await expect(page.getByTestId("active-run-identity")).toContainText(CLEAN_RUN);
    const banner = page.getByTestId("clean-run-banner");
    await expect(banner).toBeVisible();
    await expect(banner).toContainText(/no exceptions raised/i);
    await expect(banner).toContainText("15");
    // Nothing anywhere says the view failed or is empty for lack of data.
    await expect(page.getByText("Dashboard data is temporarily unavailable")).toHaveCount(0);
    await expect(page.getByText("No reconciliation run yet")).toHaveCount(0);

    // Home reports the run's own measured facts.
    const facts = page.getByTestId("home-run-facts");
    await expect(facts).toBeVisible();
    await expect(facts).toContainText("100.0%");
    await expect(facts).toContainText(/reconciled cleanly/i);
    await expect(page.getByTestId("home-run-id")).toHaveText(CLEAN_RUN);
  });

  test("each queue explains a clean run instead of looking empty by accident", async ({ page }) => {
    for (const [view, copy] of [
      ["approval_queue", /nothing to approve · clean run/i],
      ["verified_resolved", /no exceptions to resolve · clean run/i],
      ["unresolved", /no exceptions raised · clean run/i],
    ] as const) {
      await page.goto(dashboardUrl({ run: CLEAN_RUN, view }));
      await expect(page.getByText(copy)).toBeVisible();
    }
  });

  test("the clean run's matrix accounts for every record it holds", async ({ page }) => {
    await page.goto(dashboardUrl({ run: CLEAN_RUN, view: "matrix" }));

    const inventory = page.getByTestId("matrix-inventory");
    await expect(inventory).toContainText("15 records");
    await expect(inventory).toContainText("15 reconciled");
    await expect(inventory).toContainText("0 unmatched");
  });
});

/* ------------------------------------------------------------------ */
/* 4. Master matrix reports the whole population                       */
/* ------------------------------------------------------------------ */

test("the matrix distinguishes all five sources and unmatched rows", async ({ page }) => {
  const matrixResponse = await page.request.get(
    `/api/v1/runs/${EXCEPTION_RUN}/matrix?page=1&limit=25`,
  );
  expect(matrixResponse.ok()).toBeTruthy();
  const matrix = (await matrixResponse.json()) as {
    inventory: { total_records: number; unmatched_records: number };
  };
  expect(matrix.inventory.unmatched_records).toBeGreaterThan(0);

  await page.goto(dashboardUrl({ run: EXCEPTION_RUN, view: "matrix" }));

  const inventory = page.getByTestId("matrix-inventory");
  // Compare the UI with the backend census instead of pinning a stale count
  // when stricter link validation correctly exposes more unmatched records.
  await expect(inventory).toContainText(`${matrix.inventory.total_records} records`);
  await expect(inventory).toContainText(`${matrix.inventory.unmatched_records} unmatched`);
  for (const label of ["Payment", "Refund", "Settlement", "Bank entry", "Ledger entry"]) {
    await expect(inventory.getByRole("button", { name: new RegExp(label) })).toBeVisible();
  }

  // Unmatched rows are visible and labelled with the link they lack.
  await page.getByLabel("Filter by link state").selectOption("UNMATCHED");
  const rows = page.locator("tbody tr");
  await expect(rows.first()).toContainText("Unmatched");
  await expect(rows.first()).toContainText(
    /no bank entry|no match group|no settlement|no ledger|multiple bank entries|multiple ledger entries/i,
  );
  // The census still describes the whole run, never just the filtered slice.
  await expect(inventory).toContainText(`${matrix.inventory.total_records} records`);

  // A filter combination with no rows says so, rather than looking broken.
  await page.getByLabel("Filter by record type").selectOption("REFUND");
  await expect(page.getByText(/no records match this filter/i)).toBeVisible();

  // Refunds are present as their own signed record type.
  await page.getByLabel("Filter by link state").selectOption("ALL");
  await expect(page.locator("tbody tr").first()).toContainText("Refund");
  await expect(page.locator("tbody tr").first()).toContainText("−₹");
});

/* ------------------------------------------------------------------ */
/* 5. Unresolved ambiguity is preserved honestly                       */
/* ------------------------------------------------------------------ */

test("an ambiguous case stays unresolved with its recorded reason", async ({ page }) => {
  const target = await caseInStatus(page, EXCEPTION_RUN, "UNRESOLVED");
  await page.goto(dashboardUrl({ run: EXCEPTION_RUN, view: "unresolved", case: target.case_id }));

  await expect(page.getByText(/why this case is unresolved/i)).toBeVisible();
  await expect(page.getByText("NON_UNIQUE_EVIDENCE").first()).toBeVisible();
  await expect(page.getByText(/never overridden by model confidence/i)).toBeVisible();

  // The ledger view must not describe ambiguity as "no correction required".
  await page.goto(dashboardUrl({ run: EXCEPTION_RUN, view: "ledger", case: target.case_id }));
  await expect(
    page.getByText(/no correction can be previewed for an unresolved case/i),
  ).toBeVisible();
  await expect(page.getByText(/deliberately unresolved/i)).toBeVisible();
  await expect(page.getByText("No Ledger Correction Required")).toHaveCount(0);
  // No authorization is offered for a case with no verified proposal.
  await expect(page.getByRole("button", { name: /authorize simulated correction/i })).toHaveCount(0);
});

/* ------------------------------------------------------------------ */
/* 6. Verifier PASS, dry-run, approval, one-time application           */
/* ------------------------------------------------------------------ */

test.describe("the financial lifecycle", () => {
  test("verifier PASS, exact dry-run, human approval, one simulated entry", async ({ page }) => {
    const target = await caseInStatus(page, EXCEPTION_RUN, "APPROVAL_REQUIRED");
    const before = await caseDetail(page, EXCEPTION_RUN, target.case_id);
    expect(before.proof?.verifier_status).toBe("PASS");
    expect(before.dry_run).not.toBeNull();
    expect(before.simulated_correction).toBeNull();

    await page.goto(
      dashboardUrl({ run: EXCEPTION_RUN, view: "ledger", case: target.case_id }),
    );

    // Deterministic verifier PASS is shown with its rule identity.
    await expect(page.getByText(`${requiredProof(before).proof_id}`).first()).toBeVisible();
    await expect(page.getByText("PASS").first()).toBeVisible();

    // Exact signed paise before / delta / after.
    const panel = page.getByTestId("ledger-before-delta-after");
    await expect(panel).toBeVisible();
    const paise = (value: number) => {
      const negative = value < 0;
      const absolute = Math.abs(value);
      const rupees = Math.floor(absolute / 100).toLocaleString("en-IN");
      return `${negative ? "−" : ""}₹${rupees}.${(absolute % 100)
        .toString()
        .padStart(2, "0")}`;
    };
    await expect(page.getByTestId("ledger-variance-before")).toHaveText(
      paise(requiredDryRun(before).variance_before_paise),
    );
    await expect(page.getByTestId("ledger-variance-after")).toHaveText(
      paise(requiredDryRun(before).variance_after_paise),
    );
    const delta = requiredDryRun(before).proposed_delta_paise;
    await expect(page.getByTestId("ledger-proposed-delta")).toHaveText(
      delta === 0 ? paise(0) : `${delta < 0 ? "−" : "+"}${paise(Math.abs(delta))}`,
    );

    // Explicit human approval, through the dialog, bound to this proof.
    await page.getByRole("button", { name: /authorize simulated correction/i }).click();
    const identity = page.getByTestId("approval-proof-identity");
    await expect(identity).toContainText(requiredProof(before).proof_id);
    await expect(identity).toContainText(EXCEPTION_RUN);
    await page.getByRole("button", { name: /confirm authorization/i }).click();

    await expect(page.getByText(/one simulated correction entry was created/i)).toBeVisible({
      timeout: 15_000,
    });

    // Exactly one linked entry, for exactly the previewed delta.
    const after = await caseDetail(page, EXCEPTION_RUN, target.case_id);
    expect(after.case.status).toBe("SIMULATED_APPLIED");
    expect(after.simulated_correction).not.toBeNull();
    expect(requiredApplied(after).delta_paise).toBe(delta);
    await expect(page.getByTestId("ledger-applied-notice")).toContainText(
      requiredApplied(after).correction_id,
    );
  });

  test("a repeated application attempt stays idempotent", async ({ page }) => {
    const target = await caseInStatus(page, EXCEPTION_RUN, "APPROVAL_REQUIRED");
    const detail = await caseDetail(page, EXCEPTION_RUN, target.case_id);
    const body = {
      proof_id: requiredProof(detail).proof_id,
      run_id: EXCEPTION_RUN,
      reviewer_id: "e2e-idempotency",
      notes: "first",
    };

    const first = await page.request.post(`/api/v1/cases/${target.case_id}/approve`, {
      data: body,
    });
    expect(first.ok()).toBeTruthy();
    const firstBody = (await first.json()) as { correction_id: string; reused: boolean };
    expect(firstBody.reused).toBe(false);

    // A repeated click, a refresh-triggered resubmit and a retry all land here.
    for (const notes of ["duplicate click", "refresh resubmit", "request retry"]) {
      const repeat = await page.request.post(`/api/v1/cases/${target.case_id}/approve`, {
        data: { ...body, notes },
      });
      expect(repeat.ok()).toBeTruthy();
      const repeatBody = (await repeat.json()) as { correction_id: string; reused: boolean };
      expect(repeatBody.reused).toBe(true);
      expect(repeatBody.correction_id).toBe(firstBody.correction_id);
    }

    // The UI shows one entry and offers no second authorization.
    await page.goto(dashboardUrl({ run: EXCEPTION_RUN, view: "ledger", case: target.case_id }));
    await expect(page.getByTestId("ledger-applied-notice")).toContainText(
      firstBody.correction_id,
    );
    await expect(page.getByText(/never creates a second one/i)).toBeVisible();
    await expect(page.getByRole("button", { name: /authorize simulated correction/i })).toHaveCount(
      0,
    );
  });

  test("an approval naming a superseded proof is refused, not retargeted", async ({ page }) => {
    const target = await caseInStatus(page, EXCEPTION_RUN, "APPROVAL_REQUIRED");
    const refused = await page.request.post(`/api/v1/cases/${target.case_id}/approve`, {
      data: {
        proof_id: "proof-not-the-one-reviewed",
        run_id: EXCEPTION_RUN,
        reviewer_id: "e2e-spoof",
      },
    });
    expect(refused.status()).toBe(409);
    expect((await refused.json()).detail).toBe("PROOF_SUPERSEDED");

    // Nothing was written by the refused decision.
    const detail = await caseDetail(page, EXCEPTION_RUN, target.case_id);
    expect(detail.case.status).toBe("APPROVAL_REQUIRED");
    expect(detail.simulated_correction).toBeNull();
  });
});

/* ------------------------------------------------------------------ */
/* 7. Refresh and reopen restoration                                   */
/* ------------------------------------------------------------------ */

test.describe("selection restoration", () => {
  test("a refresh restores the same view, run and case", async ({ page }) => {
    const target = await caseInStatus(page, EXCEPTION_RUN, "UNRESOLVED");
    await page.goto(dashboardUrl({ run: EXCEPTION_RUN, view: "evidence", case: target.case_id }));
    await expect(page.getByText("Evidence trace", { exact: true }).first()).toBeVisible();
    await expect(page.getByTestId("active-run-identity")).toContainText(EXCEPTION_RUN);

    await page.reload();

    await expect(page.getByText("Evidence trace", { exact: true }).first()).toBeVisible();
    await expect(page.getByTestId("active-run-identity")).toContainText(EXCEPTION_RUN);
    await expect(page.locator(`text=${target.case_id}`).first()).toBeVisible();
    await expect.poll(() => new URL(page.url()).searchParams.get("case")).toBe(target.case_id);
    await expect.poll(() => new URL(page.url()).searchParams.get("run")).toBe(EXCEPTION_RUN);
  });

  test("navigating without a run pins the resolved run into the URL", async ({ page }) => {
    // Opening /dashboard resolves the active run; the URL then names that exact
    // run so reopening the link restores the same batch rather than "latest".
    await page.goto("/dashboard");
    await expect(page.getByTestId("active-run-identity")).toContainText(EXCEPTION_RUN);
    await expect
      .poll(() => new URL(page.url()).searchParams.get("run"))
      .toBe(EXCEPTION_RUN);
  });

  test("a run that no longer exists fails closed with a way back", async ({ page }) => {
    await page.goto(dashboardUrl({ run: "run-deleted-long-ago", view: "matrix" }));

    await expect(page.getByText("The selected run is no longer available")).toBeVisible();
    await expect(page.getByRole("button", { name: "Open latest run" })).toBeVisible();
    // No stale run identity and no metric is shown for the missing run.
    await expect(page.getByTestId("active-run-identity")).toHaveCount(0);
    await expect(page.getByTestId("matrix-inventory")).toHaveCount(0);

    await page.getByRole("button", { name: "Open latest run" }).click();
    await expect(page.getByTestId("active-run-identity")).toContainText(EXCEPTION_RUN);
  });

  test("a case from another run is refused rather than rendered", async ({ page }) => {
    const foreign = await caseInStatus(page, EXCEPTION_RUN, "UNRESOLVED");
    // Pin the clean run but ask for a case that belongs to the exception run.
    await page.goto(dashboardUrl({ run: CLEAN_RUN, view: "dossier", case: foreign.case_id }));

    await expect(page.getByTestId("active-run-identity")).toContainText(CLEAN_RUN);
    // The clean run has no cases, so the stale case id is simply not honoured
    // and its dossier never appears under the clean run.
    await expect(page.getByText(/no exception cases in this run/i)).toBeVisible();
    await expect(page.locator(`text=${foreign.case_id}`)).toHaveCount(0);
  });
});

/* ------------------------------------------------------------------ */
/* 8. Cross-view identity consistency                                  */
/* ------------------------------------------------------------------ */

test("every view resolves the same run and case", async ({ page }) => {
  const target = await caseInStatus(page, EXCEPTION_RUN, "UNRESOLVED");

  for (const view of [
    "home",
    "matrix",
    "dossier",
    "evidence",
    "ledger",
    "audit",
    "fee_audit",
    "unresolved",
  ]) {
    await page.goto(dashboardUrl({ run: EXCEPTION_RUN, view, case: target.case_id }));
    await expect(
      page.getByTestId("active-run-identity"),
      `${view} must show the selected run`,
    ).toContainText(EXCEPTION_RUN);
    // No other run id may appear anywhere on the page.
    await expect(page.locator(`text=${CLEAN_RUN}`), `${view} must not leak another run`).toHaveCount(
      0,
    );
  }

  // The case-bearing views all name the same case.
  for (const view of ["dossier", "evidence", "ledger", "unresolved"]) {
    await page.goto(dashboardUrl({ run: EXCEPTION_RUN, view, case: target.case_id }));
    await expect(page.locator(`text=${target.case_id}`).first()).toBeVisible();
  }
});

/* ------------------------------------------------------------------ */
/* 9. Provenance in the dossier and the export                         */
/* ------------------------------------------------------------------ */

test("the evidence trace cites source revisions and hashes", async ({ page }) => {
  const target = await caseInStatus(page, EXCEPTION_RUN, "UNRESOLVED");
  await page.goto(dashboardUrl({ run: EXCEPTION_RUN, view: "evidence", case: target.case_id }));

  await page.getByRole("button", { name: "Provenance" }).click();
  const table = page.getByRole("table", { name: /cited evidence records/i });
  await expect(table).toBeVisible();
  await expect(table).toContainText("RESOLVED");
  // Source row pointer and content digest, from the immutable source row.
  await expect(table.getByText(/^row \d+$/).first()).toBeVisible();
  await expect(table.getByText(/content digests over the imported row/i)).toHaveCount(0);
  await expect(page.getByText(/not an external attestation/i)).toBeVisible();

  // Proof provenance: rule id, rule version, canonical hash.
  await expect(page.getByText(/proof provenance/i)).toBeVisible();
  await expect(page.getByText(/canonical proof hash/i)).toBeVisible();
});

test("the exported dossier carries run provenance and synthetic labels", async ({ page }) => {
  await page.goto(dashboardUrl({ run: EXCEPTION_RUN }));
  await page.getByRole("button", { name: "Evidence Dossier" }).click();

  const dossier = page.getByRole("dialog", { name: "Run evidence dossier" });
  await expect(page.getByRole("heading", { name: "Run evidence dossier" })).toBeVisible();
  await expect(dossier.getByText(EXCEPTION_RUN).first()).toBeVisible();
  await expect(dossier.getByText("Runtime match rate", { exact: true })).toBeVisible();
  await expect(page.getByText(/not an external audit or regulatory certificate/i)).toBeVisible();
  // No certification-shaped claim survives.
  await expect(page.getByText(/100\.0% Verified/i)).toHaveCount(0);
  await expect(page.getByText(/RBI \/ FINTECH COMPLIANT/i)).toHaveCount(0);
});

test("the fee audit names its configured synthetic policy", async ({ page }) => {
  await page.goto(dashboardUrl({ run: EXCEPTION_RUN, view: "fee_audit" }));

  await expect(page.getByText(/synthetic policy · 2\.00% mdr \+ 18\.00% gst/i)).toBeVisible();
  await expect(page.getByText(/not razorpay published pricing/i).first()).toBeVisible();
  await expect(page.getByText("synthetic-merchant-fee-policy-v1")).toBeVisible();
  await expect(page.getByText(/not a universal rate card/i)).toBeVisible();
  // A caller cannot dictate the basis; the view never advertises rate inputs.
  await expect(page.getByText(/contractual merchant rate cards/i)).toHaveCount(0);
});

/* ------------------------------------------------------------------ */
/* 10. Audit trail ordering and scope                                  */
/* ------------------------------------------------------------------ */

test("the audit trail is scoped and in authoritative order", async ({ page }) => {
  const target = await caseInStatus(page, EXCEPTION_RUN, "UNRESOLVED");
  await page.goto(dashboardUrl({ run: EXCEPTION_RUN, view: "audit", case: target.case_id }));

  // Run scope and case scope are separate claims, so separate panels.
  await expect(page.getByText(`scope run ${EXCEPTION_RUN}`)).toBeVisible();
  await expect(page.getByText(new RegExp(`scope case ${target.case_id}`))).toBeVisible();
  await expect(page.getByText(/append-only · sha-256 digested · storage order/i).first()).toBeVisible();

  // Sequences ascend within the case trail.
  const sequences = await page
    .locator('[title="Append-only storage sequence"]')
    .allTextContents();
  expect(sequences.length).toBeGreaterThan(0);
  const numbers = sequences.map((text) => Number(text.replace("#", "")));
  expect(numbers).toEqual([...numbers].sort((a, b) => a - b));
});

/* ------------------------------------------------------------------ */
/* 11. Failed provider and safe failure reporting                      */
/* ------------------------------------------------------------------ */

test.describe("provider and backend failure", () => {
  test("an unavailable AI provider is reported without breaking the view", async ({ page }) => {
    // The isolated backend runs with ARGUS_AI_PROVIDER=none and every provider
    // key blanked, so the copilot has no live provider at all. It must answer
    // from persisted facts and say so, never fabricate and never error out.
    const response = await page.request.post("/api/v1/chat/message", {
      data: {
        message: "what is the match rate",
        page_context: { tab: "home", active_run_id: EXCEPTION_RUN },
      },
    });
    expect(response.ok()).toBeTruthy();
    const body = (await response.json()) as {
      provider: string;
      reply: string;
      context_summary: { active_run_id: string; scope: string };
    };
    // No live provider was used, and the response says which path answered.
    expect(body.provider).toBe("deterministic-synthesizer");
    expect(body.context_summary.active_run_id).toBe(EXCEPTION_RUN);
    expect(body.context_summary.scope).toBe("SELECTED_RUN");
    expect(body.reply).toContain(EXCEPTION_RUN);

    // API status reports the provider as unconfigured rather than green.
    await page.goto(dashboardUrl({ view: "api_status" }));
    await expect(page.getByTestId("integration-state-investigator")).toHaveText("Not configured");
  });

  test("a provider error surfaces safely and never changes financial state", async ({ page }) => {
    const target = await caseInStatus(page, EXCEPTION_RUN, "APPROVAL_REQUIRED");
    const before = await caseDetail(page, EXCEPTION_RUN, target.case_id);

    // Transport stub: force the chat endpoint to fail like a broken provider.
    await page.route("**/api/v1/chat/message", (route: Route) =>
      route.fulfill({ status: 503, json: { detail: "PROVIDER_UNAVAILABLE" } }),
    );
    await page.goto(dashboardUrl({ run: EXCEPTION_RUN, view: "home" }));
    await page.getByPlaceholder(/ask about match rates/i).fill("what is the match rate");
    await page.keyboard.press("Enter");

    // The failure is reported; no fabricated figure appears.
    await expect(page.getByText(/unable|error|failed|could not/i).first()).toBeVisible({
      timeout: 15_000,
    });
    // The run's own facts are still shown, and nothing financial moved.
    const after = await caseDetail(page, EXCEPTION_RUN, target.case_id);
    expect(after.case.status).toBe(before.case.status);
    expect(after.simulated_correction).toEqual(before.simulated_correction);
  });

  test("a backend outage fails closed and never shows a stale figure", async ({ page }) => {
    await page.goto(dashboardUrl({ run: EXCEPTION_RUN, view: "matrix" }));
    await expect(page.getByTestId("matrix-inventory")).toContainText("282 records");

    // Transport stub: the isolated backend stays healthy, so the outage is
    // reproduced at the network layer.
    await page.route("**/api/v1/runs/**", (route: Route) => route.abort("connectionrefused"));
    await page.reload();

    await expect(page.getByText("Dashboard data is temporarily unavailable")).toBeVisible();
    await expect(page.getByRole("button", { name: "Retry dashboard" })).toBeVisible();
    await expect(page.getByText("Backend unavailable", { exact: true })).toBeVisible();
    // The previously loaded inventory is not presented as current.
    await expect(page.getByTestId("matrix-inventory")).toHaveCount(0);
    await expect(page.getByText("282 records")).toHaveCount(0);
  });

  test("API status never reports configured as reachable", async ({ page }) => {
    await page.goto(dashboardUrl({ view: "api_status" }));

    await expect(page.getByText(/configured does not mean reachable/i).first()).toBeVisible();
    // The database is configured; nothing has been contacted yet.
    const database = page.getByTestId("integration-state-database");
    await expect(database).toHaveText("Configured, not probed");
    await expect(page.getByText(/no probe requested in this read/i)).toBeVisible();

    // Reachability appears only after an explicit probe, with its timestamp.
    await page
      .getByRole("listitem")
      .filter({ hasText: "Local SQLite persistence" })
      .getByRole("button", { name: "Probe now" })
      .click();
    await expect(database).toHaveText("Reachable", { timeout: 15_000 });
    await expect(page.getByText(/probed database/i)).toBeVisible();

    // A normal re-read and remount preserve the last result from this backend
    // process without performing another probe.
    await page.getByRole("button", { name: "Re-read configuration" }).click();
    await expect(database).toHaveText("Reachable");
    await page.goto(dashboardUrl({ view: "home" }));
    await page.goto(dashboardUrl({ view: "api_status" }));
    await expect(page.getByTestId("integration-state-database")).toHaveText("Reachable");
    const restoredDatabase = page
      .getByRole("listitem")
      .filter({ hasText: "Local SQLite persistence" });
    await expect(restoredDatabase.getByText("never", { exact: true })).toHaveCount(0);
  });
});

/* ------------------------------------------------------------------ */
/* 12. No stale response replaces a newer selection                    */
/* ------------------------------------------------------------------ */

test.describe("stale responses", () => {
  test("a slow case response cannot replace a newer case selection", async ({ page }) => {
    const first = await caseInStatus(page, EXCEPTION_RUN, "UNRESOLVED");
    const listing = await page.request.get(
      `/api/v1/runs/${EXCEPTION_RUN}/cases?status=UNRESOLVED`,
    );
    const unresolved = (await listing.json()) as Array<{ case_id: string }>;
    const second = unresolved[1];
    if (second === undefined) throw new Error("fixture needs two unresolved cases");

    // Transport stub: hold the FIRST case's detail response until after the
    // second case has been selected, reproducing an out-of-order arrival.
    const gate = deferred();
    await page.route(`**/api/v1/cases/${first.case_id}?**`, async (route: Route) => {
      await gate.promise;
      await route.continue();
    });

    await page.goto(
      dashboardUrl({ run: EXCEPTION_RUN, view: "dossier", case: first.case_id }),
    );
    // Select the second case while the first is still in flight.
    await page.locator(`text=${second.case_id}`).first().click();
    await expect(page.locator("main").getByText(second.case_id).first()).toBeVisible({
      timeout: 15_000,
    });

    // Now let the stale first response land. It must be discarded.
    gate.release();
    await page.waitForTimeout(1_000);
    expect(new URL(page.url()).searchParams.get("case")).toBe(second.case_id);
    await expect(page.locator("main").getByText(second.case_id).first()).toBeVisible();
    await expect(page.locator("main").getByText(first.case_id)).toHaveCount(0);
  });

  test("a slow run response cannot replace a newer run selection", async ({ page }) => {
    // Transport stub: hold the clean run's summary, then switch to the
    // exception run before releasing it.
    const gate = deferred();
    await page.route(`**/api/v1/runs/${CLEAN_RUN}/summary`, async (route: Route) => {
      await gate.promise;
      await route.continue();
    });

    await page.goto(dashboardUrl({ run: CLEAN_RUN }));
    await page.evaluate(
      (target) => window.history.replaceState(null, "", target),
      dashboardUrl({ run: EXCEPTION_RUN }),
    );
    await page.reload();
    await expect(page.getByTestId("active-run-identity")).toContainText(EXCEPTION_RUN);

    gate.release();
    await page.waitForTimeout(1_000);
    // The newer selection stands; the clean run never takes over.
    await expect(page.getByTestId("active-run-identity")).toContainText(EXCEPTION_RUN);
    await expect(page.getByTestId("clean-run-banner")).toHaveCount(0);
  });
});

/* ------------------------------------------------------------------ */
/* 13. Imported records stay distinct from exception cases             */
/* ------------------------------------------------------------------ */

test("imported gateway records are never presented as exception cases", async ({ page }) => {
  const demo = scenario("demo_active");
  expect(demo.import_id).toBeTruthy();

  // The gateway import is evidence intake; it creates no reconciliation case.
  const cases = await page.request.get(`/api/v1/runs/${EXCEPTION_RUN}/cases`);
  const caseIds = ((await cases.json()) as Array<{ case_id: string }>).map((c) => c.case_id);
  expect(caseIds.every((id) => id.startsWith("case-"))).toBe(true);
  expect(caseIds).not.toContain(demo.import_id);

  // The matrix lists imported records as records, with their own link state,
  // and never as cases.
  await page.goto(dashboardUrl({ run: EXCEPTION_RUN, view: "matrix" }));
  const rows = page.locator("tbody tr");
  await expect(rows.first()).toContainText(/Payment|Refund|Settlement|Bank entry|Ledger entry/);
  await expect(page.locator("tbody").getByText(/^case-/)).toHaveCount(0);
});
