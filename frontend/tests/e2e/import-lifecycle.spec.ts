import { expect, test, type Page } from "@playwright/test";
import { linked, scenario, withImport } from "./fixture";

/**
 * Import dialog lifecycle, against a real isolated backend.
 *
 * These cover what the pure reducer and view tests cannot: the component
 * actually mounting, fetching, and rendering. Each test starts from a cold page
 * load that has never seen a sync response, which is exactly the state a
 * browser refresh produces.
 */

const IMPORT_SESSION_KEY = "argus_import_session_v1";
const BACKEND_ORIGIN = process.env.ARGUS_E2E_BACKEND_ORIGIN ?? "http://127.0.0.1:8000";

// Every browser API request stays local. Unknown external requests cannot use
// any credentials from the host. Lifecycle fault injection is explicit below.
test.beforeEach(async ({ page }) => {
  await page.route("**/*", route => {
    const url = new URL(route.request().url());
    return ["127.0.0.1", "localhost"].includes(url.hostname) ? route.continue() : route.abort();
  });
});

/** Load the dashboard as if this browser had been using `sessionId` all along. */
async function openDialogFor(page: Page, sessionId: string): Promise<void> {
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

/**
 * The intake card header line.
 *
 * Matched on the "Import <id>" label rather than the bare id, because the id
 * also appears inside every staged source filename further down the dialog.
 */
function intakeHeader(page: Page, importId: string) {
  return page.getByText(`Import ${importId}`, { exact: true });
}

test.describe("cold restore of a linked import", () => {
  test("rebuilds the intake card from the backend after a refresh", async ({ page }) => {
    const entry = withImport("demo_active");
    await openDialogFor(page, entry.session_id);

    // The dialog never saw a sync response, so everything here came from the
    // persisted snapshot.
    await expect(intakeHeader(page, entry.import_id)).toBeVisible();
    await expect(page.getByText(/restored from backend/i).first()).toBeVisible();
    await page.getByText("Import details & payment records", { exact: true }).click();
    await expect(
      page.getByText("Credentials were never persisted", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText(/2 of 2 payments reconciliation-eligible/i)).toBeVisible();
  });

  test("restores an active demo bundle with its provenance", async ({ page }) => {
    const entry = linked("demo_active");
    await openDialogFor(page, entry.session_id);

    await expect(page.getByText("Synthetic demo chain active")).toBeVisible();
    await expect(page.getByText(/not production eligible/i)).toBeVisible();
    await page.getByText("Import details & payment records", { exact: true }).click();
    await expect(page.getByText(entry.evidence_id, { exact: false })).toBeVisible();
    await expect(page.getByText(/provenance SYNTHETIC_DEMO · ACTIVE/i)).toBeVisible();
  });

  // REVIEW-002: generation history must not be presented as active evidence.
  test("reports a superseded source instead of claiming a full active chain", async ({ page }) => {
    const entry = scenario("demo_superseded");
    await openDialogFor(page, entry.session_id);

    await expect(page.getByText("Synthetic demo chain partially active")).toBeVisible();
    await expect(page.getByText(/replaced since generation: settlements/i)).toBeVisible();
    // The synthetic provenance still present in the session is not hidden.
    await expect(page.getByText(/mixes synthetic and other evidence/i)).toBeVisible();
    await expect(page.getByText("Synthetic demo chain active", { exact: true })).toHaveCount(0);
  });
});

// REVIEW-004: every figure must describe the same defined population.
test.describe("mixed payment population", () => {
  test("describes payment records, not captured payments, with scoped counts", async ({ page }) => {
    await openDialogFor(page, scenario("mixed_population").session_id);
    await page.getByText("Import details & payment records", { exact: true }).click();

    // One captured payment, one failed payment, one processed refund. The
    // dossier holds both payments; only one is eligible and pending.
    await expect(
      page.getByText(
        /Preview of 2 of 2 payment records in this import · 1 reconciliation-eligible · 1 awaiting Razorpay settlement · 1 not eligible\./i,
      ),
    ).toBeVisible();
    // The refund is not counted as a pending payment anywhere on screen.
    await expect(page.getByText(/2 awaiting Razorpay settlement/i)).toHaveCount(0);
  });

  test("shows each preview row status so a failed payment is visible", async ({ page }) => {
    await openDialogFor(page, scenario("mixed_population").session_id);
    await page.getByText("Import details & payment records", { exact: true }).click();

    await expect(page.getByText(/all payment records/i)).toBeVisible();
    await expect(page.getByText("captured", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("failed", { exact: true }).first()).toBeVisible();
  });
});

test.describe("demo generation from a cold-reopened session", () => {
  // REVIEW/C6: this action was a silent no-op before the import was restored.
  test("generates labelled evidence without a fresh sync in this page load", async ({ page }) => {
    await openDialogFor(page, scenario("pending").session_id);

    await expect(page.getByText(/awaiting settlement evidence/i)).toBeVisible();
    const entry = withImport("pending");
    const importUrl = `/api/v1/razorpay/imports/${entry.import_id}?session_id=${entry.session_id}`;
    const original = await (await page.request.get(importUrl)).json();
    const generate = page.getByRole("button", { name: /generate synthetic gateway evidence/i });
    await expect(generate).toBeEnabled();
    await generate.click();

    await expect(page.getByText("Synthetic gateway evidence active")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/provenance SYNTHETIC_DEMO/i)).toBeVisible();
    // Readiness now comes from current session status, not the sync response.
    await expect(page.getByText(/awaiting settlement evidence/i)).toHaveCount(0);
    await expect(page.getByText("1/3 sources ready", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Waiting for 2 sources" })).toBeDisabled();
    await expect(page.getByText("Bank and ledger readiness comes from your separate uploads.")).toBeVisible();
    const gatewayStatus = await (await page.request.get(`/api/v1/ingest/sessions/${entry.session_id}/status`)).json();
    expect(Object.keys(gatewayStatus.active_sources).sort()).toEqual(["payments", "refunds", "settlements"]);
    const uploaded = [
      {
        heading: "Bank statement", ready: "2/3 sources ready", name: "synthetic-bank.csv",
        csv: "bank_entry_id,posted_at_utc,value_date,currency,signed_amount,narration,utr,account_fingerprint\n" +
          "bank_synthetic,2026-03-01T09:00:00Z,2026-03-01,INR,100.00,Synthetic fixture,DEMO123,SYNTHETIC-BANK\n",
      },
      {
        heading: "Merchant ledger", ready: "3/3 sources ready", name: "synthetic-ledger.csv",
        csv: "ledger_entry_id,account_code,accounting_date,currency,signed_amount,source_reference,source_type,description,entry_origin\n" +
          "ledger_synthetic,2100-PAYMENTS-CLEARING,2026-03-01,INR,100.00,pay_e2e_000,PAYMENT,Synthetic fixture,IMPORTED\n",
      },
    ];
    for (const upload of uploaded) {
      const card = page.locator("article").filter({ has: page.getByRole("heading", { name: upload.heading, exact: true }) });
      const chooser = page.waitForEvent("filechooser");
      await card.getByRole("button", { name: "Choose CSV" }).click();
      await (await chooser).setFiles({ name: upload.name, mimeType: "text/csv", buffer: Buffer.from(upload.csv) });
      await page.getByRole("button", { name: "Activate revision & validate" }).click();
      await expect(page.getByText(upload.ready, { exact: true })).toBeVisible();
      await expect(card.getByText(upload.name, { exact: true })).toBeVisible();
      await expect(card.getByText(/Your upload · saved in this session/)).toBeVisible();
    }
    await expect(page.getByRole("button", { name: "Run rules-only reconciliation" })).toBeEnabled();
    const merchantBefore = await (await page.request.get(`/api/v1/ingest/sessions/${entry.session_id}/status`)).json();
    const repeated = await page.request.post(`/api/v1/razorpay/imports/${entry.import_id}/generate-gateway-evidence`, { data: {session_id: entry.session_id} });
    expect(repeated.ok()).toBeTruthy();
    const merchantAfter = await (await page.request.get(`/api/v1/ingest/sessions/${entry.session_id}/status`)).json();
    for (const source of ["bank_entries", "ledger_entries"]) {
      expect(merchantAfter.active_sources[source]).toEqual(merchantBefore.active_sources[source]);
    }
    const after = await (await page.request.get(importUrl)).json();
    expect(after.counts).toEqual(original.counts);
    expect(after.counts.SETTLEMENT ?? 0).toBe(0);
    expect(after.counts.SETTLEMENT_RECON ?? 0).toBe(0);
    expect(after.demo_evidence.scope).toBe("GATEWAY_ONLY");
    expect(after.demo_evidence.activation_state).toBe("ACTIVE");
    await page.reload();
    await page.getByRole("button", { name: /import data/i }).click();
    await expect(page.getByText("Synthetic gateway evidence active")).toBeVisible();
    await expect(page.getByText("3/3 sources ready", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Run rules-only reconciliation" })).toBeEnabled();
  });
});

test.describe("cross-session isolation", () => {
  // REVIEW-003: one session demo must never appear in another.
  test("an unrelated session shows no import and no demo badge", async ({ page }) => {
    await openDialogFor(page, scenario("empty").session_id);

    await expect(page.getByText(/^Import gwi-/)).toHaveCount(0);
    await expect(page.getByText(/synthetic demo/i)).toHaveCount(0);
    await expect(page.getByText(/restored from backend/i)).toHaveCount(0);
  });

  test("reopening re-reads the backend rather than reusing dialog memory", async ({ page }) => {
    const entry = withImport("demo_active");
    await openDialogFor(page, entry.session_id);
    await expect(page.getByText("Synthetic demo chain active")).toBeVisible();

    await page.getByRole("button", { name: "Close", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Razorpay Test Mode" })).toHaveCount(0);

    await page.getByRole("button", { name: /import data/i }).click();
    await expect(intakeHeader(page, entry.import_id)).toBeVisible();
    await expect(page.getByText(/restored from backend/i).first()).toBeVisible();
  });
});

test.describe("a failed import", () => {
  // REVIEW-003: a failure must not discard the still-valid earlier session.
  test("keeps the previous import visible and shows the error", async ({ page }) => {
    const entry = linked("demo_active");
    // Only the sync endpoint is stubbed, so no outbound Razorpay call is made.
    await page.route("**/api/v1/razorpay/sync", (route) =>
      route.fulfill({
        status: 502,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Razorpay payments API failed: stubbed for test." }),
      }),
    );
    await openDialogFor(page, entry.session_id);
    await expect(intakeHeader(page, entry.import_id)).toBeVisible();

    await page.getByText("Import another period", { exact: true }).click();
    await page.getByLabel(/test key id/i).fill("rzp_test_stub");
    await page.getByLabel(/test key secret/i).fill("stub-secret");
    await page.getByRole("button", { name: /connect and retrieve razorpay data/i }).click();

    await expect(page.getByText(/stubbed for test/i)).toBeVisible();
    // The earlier session survives: same import, no blank or mixed panel.
    await expect(intakeHeader(page, entry.import_id)).toBeVisible();
    await expect(page.getByText(/restored from backend/i).first()).toBeVisible();
    await page.getByText("Import details & payment records", { exact: true }).click();
    await expect(page.getByText(entry.evidence_id, { exact: false })).toBeVisible();
    // Credentials are cleared from the form regardless of the outcome.
    await expect(page.getByLabel(/test key id/i)).toHaveValue("");
    await expect(page.getByLabel(/test key secret/i)).toHaveValue("");
  });
});

test("manual bank replacement updates activation without closing the dialog", async ({ page }) => {
  await openDialogFor(page, scenario("manual_replace").session_id);
  await expect(page.getByText("Synthetic demo chain active", {exact:true})).toBeVisible();
  const bank = page.locator("article").filter({has:page.getByRole("heading",{name:"Bank statement",exact:true})});
  const chooser = page.waitForEvent("filechooser");
  await expect(page.getByText("Separate merchant uploads required", {exact:true})).toBeVisible();
  await expect(page.getByText("1/3 sources ready", {exact:true})).toBeVisible();
  await bank.getByRole("button",{name:"Choose CSV"}).click();
  await (await chooser).setFiles({name:"synthetic-bank.csv",mimeType:"text/csv",buffer:Buffer.from(
    "bank_entry_id,posted_at_utc,value_date,currency,signed_amount,narration,utr,account_fingerprint\n" +
    "bank_synthetic,2026-02-28T09:00:00Z,2026-02-28,INR,100.00,Synthetic test,DEMO123,SYNTHETIC-BANK\n",
  )});
  await page.getByRole("button",{name:"Activate revision & validate"}).click();
  await expect(page.getByText("Synthetic demo chain partially active",{exact:true})).toBeVisible();
  await expect(page.getByText(/replaced since generation: bank_entries/i)).toBeVisible();
  await expect(page.getByText("Synthetic demo chain active",{exact:true})).toHaveCount(0);
});

test("captured payments missing financial fields cannot generate demo evidence", async ({ page }) => {
  await openDialogFor(page, scenario("missing_fields").session_id);
  await expect(page.getByRole("button",{name:/generate synthetic gateway evidence/i})).toBeDisabled();
  await expect(page.getByText(/cannot safely generate a demo bundle/i)).toBeVisible();
});

test("late pre-close status cannot overwrite a reopened session", async ({ page }) => {
  const entry=withImport("demo_active");
  let release!: () => void;
  const held=new Promise<void>(resolve=>{release=resolve;});
  let arrived!: () => void;
  const arrival=new Promise<void>(resolve=>{arrived=resolve;});
  let first=true;
  await page.route(`**/ingest/sessions/${entry.session_id}/status`,async route=>{
    if(!first) return route.fallback();
    first=false;
    const response=await route.fetch();
    const status=await response.json();
    arrived(); await held;
    await route.fulfill({response,json:{...status,gateway_import_id:null}});
  });
  await openDialogFor(page,entry.session_id);
  await arrival;
  await page.getByRole("button",{name:"Close",exact:true}).click();
  await page.getByRole("button",{name:/import data/i}).click();
  await expect(intakeHeader(page,entry.import_id)).toBeVisible();
  const delayedResponse = page.waitForResponse(response => response.url().includes(`/sessions/${entry.session_id}/status`));
  release();
  await (await delayedResponse).finished();
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  // The resumed response is a real fetch with a deliberately stale link.
  await expect(page.getByText("Synthetic demo chain active",{exact:true})).toBeVisible();
});

test("late pre-close detail cannot replace the newly loaded activation", async ({ page }) => {
  const entry=withImport("demo_active");
  let release!: () => void;
  const held=new Promise<void>(resolve=>{release=resolve;});
  let arrived!: () => void;
  const arrival=new Promise<void>(resolve=>{arrived=resolve;});
  let first=true;
  await page.route(`**/razorpay/imports/${entry.import_id}?**`,async route=>{
    if(!first) return route.fallback();
    first=false;
    const response=await route.fetch();
    const detail=await response.json();
    arrived(); await held;
    await route.fulfill({response,json:{...detail,demo_evidence:null}});
  });
  await openDialogFor(page,entry.session_id); await arrival;
  await page.getByRole("button",{name:"Close",exact:true}).click();
  await page.getByRole("button",{name:/import data/i}).click();
  await expect(page.getByText("Synthetic demo chain active",{exact:true})).toBeVisible();
  const delayedResponse = page.waitForResponse(response => response.url().includes(`/imports/${entry.import_id}?`));
  release();
  await (await delayedResponse).finished();
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(page.getByText("Synthetic demo chain active",{exact:true})).toBeVisible();
});

test("successful A to B import never retains A demo", async ({ page }) => {
  const a=linked("demo_active"), b=withImport("mixed_population");
  let switched=false;
  await page.route(`**/ingest/sessions/${a.session_id}/status`,async route=>{
    if(!switched) return route.fallback();
    const response=await route.fetch({url:`${BACKEND_ORIGIN}/api/v1/ingest/sessions/${b.session_id}/status`});
    await route.fulfill({response});
  });
  await page.route("**/api/v1/razorpay/sync",async route=>{
    const response=await route.fetch({url:`${BACKEND_ORIGIN}/api/v1/razorpay/imports/${b.import_id}`,method:"GET",postData:undefined});
    const d=await response.json();
    switched=true;
    await route.fulfill({status:200,json:{...d,
      orders_count:d.counts.ORDER ?? 0,payments_count:d.counts.PAYMENT ?? 0,
      refunds_count:d.counts.REFUND ?? 0,settlements_count:0,settlement_reconciliation_count:0,
      message:"Synthetic successful import fixture",settlement_reconciliation_required:true,
    }});
  });
  await openDialogFor(page,a.session_id);
  await expect(page.getByText("Synthetic demo chain active",{exact:true})).toBeVisible();
  await page.getByText("Import another period", { exact: true }).click();
  await page.getByLabel(/test key id/i).fill("rzp_test_stub");
  await page.getByLabel(/test key secret/i).fill("stub-secret");
  await page.getByRole("button",{name:/connect and retrieve razorpay data/i}).click();
  await expect(intakeHeader(page,b.import_id)).toBeVisible();
  await expect(page.getByRole("button",{name:/generate synthetic gateway evidence/i})).toBeEnabled();
  await expect(page.getByText(a.evidence_id,{exact:false})).toHaveCount(0);
});

test("an outdated backend cannot fall back to five-source generation", async ({ page }) => {
  // Explicit version-skew fault injection; no real generation is performed.
  let legacyCalls = 0;
  await page.route("**/generate-demo-evidence", route => {
    legacyCalls += 1;
    return route.abort();
  });
  await page.route("**/generate-gateway-evidence", route => route.fulfill({
    status: 404, json: {detail: "Not Found"},
  }));
  const entry = withImport("mixed_population");
  await openDialogFor(page, entry.session_id);
  const url = `/api/v1/ingest/sessions/${entry.session_id}/status`;
  const before = await (await page.request.get(url)).json();
  await page.getByRole("button", {name: /generate synthetic gateway evidence/i}).click();
  await expect(page.getByText(/Restart the backend with the updated code; no legacy generation was attempted/)).toBeVisible();
  expect(legacyCalls).toBe(0);
  const after = await (await page.request.get(url)).json();
  expect(after.active_sources).toEqual(before.active_sources);
  expect(after.ready_source_groups).toBe(before.ready_source_groups);
});

test("compact mobile intake keeps provenance visible and details expandable", async ({ page }) => {
  await page.setViewportSize({width: 390, height: 844});
  await openDialogFor(page, scenario("demo_active").session_id);
  const dialog = page.getByRole("dialog", {name: "Import evidence", exact: true});
  await expect(page.getByText("Separate merchant uploads required", {exact:true})).toBeVisible();
  await expect(page.getByText(/not production eligible/i)).toBeVisible();
  await expect(page.getByText("Credentials were never persisted", {exact:true})).toBeHidden();
  await page.getByText("Import details & payment records", {exact:true}).click();
  await expect(page.getByText("Credentials were never persisted", {exact:true})).toBeVisible();
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  const bounds = await dialog.boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  await expect(page.getByRole("button", {name: "Waiting for 2 sources"})).toBeInViewport();
});

test("import dialog contains keyboard focus and restores its trigger", async ({ page }) => {
  await openDialogFor(page, scenario("empty").session_id);
  const close = page.getByRole("button", {name:"Close", exact:true});
  await expect(close).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(page.getByRole("combobox", {name:"Reconciliation execution mode"})).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(close).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", {name:"Import evidence", exact:true})).toHaveCount(0);
  await expect(page.getByRole("button", {name:"Import Data", exact:true})).toBeFocused();
});
