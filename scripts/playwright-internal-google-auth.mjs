import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { chromium, request } from "playwright";

const python = process.env.PYTHON || process.env.PYTHON_EXECUTABLE || "python";
const harness = spawn(python, ["tests/internal_google_browser_harness.py"], {
  cwd: process.cwd(),
  env: { ...process.env, PYTHONUNBUFFERED: "1" },
  stdio: ["ignore", "pipe", "pipe"],
});

let stderr = "";
harness.stderr.on("data", (chunk) => {
  stderr += chunk.toString();
});

const baseUrl = await new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error("Synthetic auth harness did not start.")), 10000);
  harness.stdout.once("data", (chunk) => {
    clearTimeout(timer);
    resolve(chunk.toString().trim().split(/\r?\n/, 1)[0]);
  });
  harness.once("exit", (code) => {
    clearTimeout(timer);
    reject(new Error(`Synthetic auth harness exited early (${code}). ${stderr}`));
  });
});

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const anonymousApi = await request.newContext();
let authenticatedApi;
const consoleProblems = [];
page.on("console", (message) => {
  if (["error", "warning"].includes(message.type())) {
    const location = message.location().url;
    consoleProblems.push(location ? `${message.text()} (${location})` : message.text());
  }
});
page.on("pageerror", (error) => consoleProblems.push(error.message));
await page.route("**/api/quote-sessions", async (route) => {
  if (route.request().method() === "GET") {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ quote_sessions: [] }),
    });
    return;
  }
  await route.fallback();
});
await page.route("**/api/log", (route) => route.fulfill({ status: 204, body: "" }));

try {
  const denied = await anonymousApi.get(`${baseUrl}/api/session`);
  if (denied.status() !== 401) throw new Error(`Expected unauthenticated 401, got ${denied.status()}.`);

  for (const claimCase of [
    "unknown-sub",
    "same-email-different-sub",
    "same-sub-different-email",
  ]) {
    const selected = await anonymousApi.get(
      `${baseUrl}/__synthetic_oidc/case?value=${encodeURIComponent(claimCase)}`,
    );
    if (selected.status() !== 200) throw new Error(`Could not select ${claimCase}.`);
    const deniedContext = await browser.newContext();
    const deniedPage = await deniedContext.newPage();
    const deniedResponse = await deniedPage.goto(`${baseUrl}/login`, {
      waitUntil: "domcontentloaded",
      timeout: 15000,
    });
    if (!deniedResponse || deniedResponse.status() !== 403) {
      throw new Error(`${claimCase} was not rejected before session creation.`);
    }
    const cookies = await deniedContext.cookies(baseUrl);
    if (cookies.some((cookie) => cookie.name === "swooshz_quote_session")) {
      throw new Error(`${claimCase} received a session cookie.`);
    }
    const protectedResponse = await deniedContext.request.get(`${baseUrl}/api/session`);
    if (protectedResponse.status() !== 401) {
      throw new Error(`${claimCase} reached a protected API.`);
    }
    await deniedContext.close();
  }
  const approvedCase = await anonymousApi.get(
    `${baseUrl}/__synthetic_oidc/case?value=approved`,
  );
  if (approvedCase.status() !== 200) throw new Error("Could not restore approved identity.");

  await page.goto(`${baseUrl}/login`, { waitUntil: "domcontentloaded", timeout: 15000 });
  if (new URL(page.url()).origin !== new URL(baseUrl).origin) {
    throw new Error("Synthetic OIDC flow escaped the local harness.");
  }

  const session = await page.evaluate(async () => {
    const response = await fetch("/api/session");
    return { status: response.status, body: await response.json() };
  });
  if (session.status !== 200 || !session.body.authenticated) {
    throw new Error("Synthetic exact-allowlist authentication did not create a session.");
  }
  if (session.body.user?.subject !== "synthetic-browser-subject") {
    throw new Error("Stable synthetic subject was not the primary session identity.");
  }

  authenticatedApi = await request.newContext({
    storageState: await page.context().storageState(),
  });
  const unsafeLogout = await authenticatedApi.get(`${baseUrl}/logout`);
  if (unsafeLogout.status() !== 405) throw new Error("GET logout was not rejected.");

  const csrfHeaders = { [session.body.csrf_header]: session.body.csrf_token };
  const pricingSave = await authenticatedApi.post(`${baseUrl}/api/settings/pricing-references`, {
    headers: csrfHeaders,
    data: {
      id: "synthetic-run560-pricing",
      label: "Synthetic Run 560 Pricing",
      source: "company",
      currency: "SGD",
      tax: { label: "GST", rate: 0.09 },
      items: [{
        id: "synthetic-run560-row",
        section: "Synthetic",
        description: "Synthetic protected export row",
        unit_hint: "unit",
        internal_cost: 10,
        markup_multiplier: 1.5,
        pricing_evidence: {
          source_type: "supplier_quote",
          source_reference: "synthetic-run560-evidence",
          captured_at: "2026-09-15T00:00:00Z",
        },
      }],
    },
  });
  const pricingBody = await pricingSave.json();
  if (pricingSave.status() !== 200) {
    throw new Error(`Synthetic pricing setup failed: ${pricingSave.status()} ${JSON.stringify(pricingBody)}.`);
  }
  const pricingDigest = String(pricingBody.pricing_reference?.digest_sha256 || "");
  if (!/^sha256:[0-9a-f]{64}$/i.test(pricingDigest)) {
    throw new Error("Synthetic pricing setup did not return a canonical digest.");
  }
  const auditCounts = async () => {
    const response = await authenticatedApi.get(`${baseUrl}/__run560/artifact-audit`);
    const body = await response.json();
    return (body.events || []).reduce((counts, event) => {
      counts[event.stage] = (counts[event.stage] || 0) + 1;
      return counts;
    }, { version: 0, artifact: 0 });
  };
  const publicationReceipt = async (sessionId) => {
    const response = await authenticatedApi.get(
      `${baseUrl}/__run560/publication-receipt?session_id=${encodeURIComponent(sessionId)}`,
    );
    if (response.status() !== 200) throw new Error(`Publication receipt failed: ${response.status()}.`);
    return response.json();
  };
  const draftState = (revision = 1) => ({
    version: 5,
    outputRevision: revision,
    outputRows: [{
      source_basis_line_id: "run560-row",
      section: "Synthetic",
      description: "Synthetic protected export row",
      quantity: 1,
      unit: "unit",
      price_mode: "Priced",
      unit_price_override: revision === 1 ? 15 : 16,
      basis_order: 1,
      category_order: 1,
      item_order: 1,
    }],
    pricingMatches: [],
    lineItems: [],
  });
  const generationPayload = (sessionId, revision = 1) => ({
    quote_session: { session_id: sessionId, status: { quote_generated: false } },
    client: { name: "Synthetic Run 560 Customer" },
    project: { title: "Synthetic Run 560 Protected Export" },
    company: { name: "Synthetic Run 560 Company" },
    profile_id: "synthetic-run560-company",
    pricing_reference_id: "synthetic-run560-pricing",
    pricing_reference_source: "company",
    pricing_reference: {
      id: "synthetic-run560-pricing",
      source: "company",
      currency: "SGD",
      digest_sha256: pricingDigest,
    },
    output_revision: revision,
    line_items: draftState(revision).outputRows,
  });
  const materialize = async (path, sessionId, runId, payload = generationPayload(sessionId)) => {
    const response = await authenticatedApi.post(`${baseUrl}${path}`, {
      headers: csrfHeaders,
      data: { session_id: sessionId, run_id: runId, generation_payload: payload },
    });
    const body = await response.json();
    if (response.status() !== 200 || body.status !== "ok") {
      throw new Error(`Publication fixture failed: ${response.status()} ${JSON.stringify(body)}.`);
    }
    return body;
  };

  const sessionA = "quote-run560-auth-a";
  const runA = "run-run560-auth-a";
  const expectedA = await materialize("/__run560/materialize-publication", sessionA, runA);
  const receiptA = await publicationReceipt(sessionA);
  if (
    receiptA.publication?.state !== "published"
    || receiptA.version?.state !== "published"
    || receiptA.version?.session_id !== sessionA
    || receiptA.xlsx?.sha256 !== expectedA.sha256
    || Number(receiptA.xlsx?.size_bytes) !== expectedA.size_bytes
  ) {
    throw new Error(`Current publication receipt is incomplete: ${JSON.stringify(receiptA)}.`);
  }
  const currentAuditBefore = await auditCounts();
  const currentDownload = await authenticatedApi.get(
    `${baseUrl}/api/quote-sessions/${sessionA}/download/xlsx`,
  );
  const currentBytes = await currentDownload.body();
  const currentAuditAfter = await auditCounts();
  if (
    currentDownload.status() !== 200
    || currentBytes.length !== expectedA.size_bytes
    || createHash("sha256").update(currentBytes).digest("hex") !== expectedA.sha256
    || currentAuditAfter.version !== currentAuditBefore.version + 1
    || currentAuditAfter.artifact !== currentAuditBefore.artifact + 1
  ) {
    throw new Error("Current protected publication did not return genuine verified bytes.");
  }

  const otherSession = "quote-run560-auth-other";
  const otherRun = "run-run560-auth-other";
  await materialize("/__run560/materialize-publication", otherSession, otherRun);
  const wrongTarget = "quote-run560-wrong-version";
  const wrongFixture = await authenticatedApi.post(`${baseUrl}/__run560/clone-wrong-version`, {
    headers: csrfHeaders,
    data: { source_session_id: sessionA, target_session_id: wrongTarget, other_run_id: otherRun },
  });
  if (wrongFixture.status() !== 200) throw new Error("Wrong-session version fixture could not be created.");
  const wrongAuditBefore = await auditCounts();
  const wrongDownload = await authenticatedApi.get(
    `${baseUrl}/api/quote-sessions/${wrongTarget}/download/xlsx`,
  );
  const wrongAuditAfter = await auditCounts();
  if (
    wrongDownload.status() !== 404
    || wrongAuditAfter.version !== wrongAuditBefore.version + 1
    || wrongAuditAfter.artifact !== wrongAuditBefore.artifact
  ) {
    throw new Error("Wrong-session publication version reached artifact retrieval.");
  }

  const detailResponse = await authenticatedApi.get(`${baseUrl}/api/quote-sessions/${sessionA}`);
  const detailBody = await detailResponse.json();
  const editedDraft = structuredClone(detailBody.quote_session?.draft_state || {});
  editedDraft.outputRevision = 2;
  editedDraft.outputRows[0].unit_price_override = 16;
  editedDraft.pricingMatches[0].unit_price_override = 16;
  editedDraft.lineItems[0].unit_price_override = 16;
  const editPayload = {
    session_id: sessionA,
    customer_summary: { customer_name: "Synthetic Run 560 Customer", project_name: "Synthetic Run 560 Protected Export" },
    quote_company_profile: { id: "synthetic", display_name: "Synthetic Run 560 Company" },
    pricing_reference: { id: "synthetic-run560-pricing", display_name: "Synthetic", source: "company" },
    commercials: { currency: "SGD", exchange_rate: 1, subtotal: 16, tax_label: "GST", tax_rate: 0.09, tax_amount: 1.44, grand_total: 17.44 },
    status: { quote_generated: true },
    draft_state: editedDraft,
  };
  const edited = await authenticatedApi.post(`${baseUrl}/api/quote-sessions`, {
    headers: csrfHeaders,
    data: editPayload,
  });
  const editedBody = await edited.json();
  if (edited.status() !== 200) throw new Error(`Commercial edit save failed: ${edited.status()} ${JSON.stringify(editedBody)}.`);
  if (Number(editedBody.quote_session?.commercials?.subtotal) !== 16) {
    throw new Error("Commercial subtotal edit did not persist through the production quote-session route.");
  }
  const cycleTwo = await authenticatedApi.post(`${baseUrl}/api/quote-sessions`, {
    headers: csrfHeaders,
    data: editPayload,
  });
  if (cycleTwo.status() !== 200) throw new Error("Second server-backed persistence cycle failed.");
  const staleAuditBefore = await auditCounts();
  const staleDownload = await authenticatedApi.get(
    `${baseUrl}/api/quote-sessions/${sessionA}/download/xlsx`,
  );
  const staleAuditAfter = await auditCounts();
  if (
    staleDownload.status() !== 404
    || staleAuditAfter.version !== staleAuditBefore.version
    || staleAuditAfter.artifact !== staleAuditBefore.artifact
  ) {
    throw new Error("Stale export was not rejected before version/artifact lookup.");
  }

  const runB = "run-run560-auth-b";
  await materialize("/__run560/materialize-publication", sessionA, runB, generationPayload(sessionA, 2));
  const receiptB = await publicationReceipt(sessionA);
  if (receiptB.publication?.run_id !== runB || receiptB.version?.state !== "published") {
    throw new Error("Publication B did not supersede publication A.");
  }
  const restoredA = await authenticatedApi.post(`${baseUrl}/__run560/restore-version-metadata`, {
    headers: csrfHeaders,
    data: { session_id: sessionA, run_id: runA },
  });
  if (restoredA.status() !== 200) throw new Error("Publication A metadata could not be restored for proof.");
  const supersededAuditBefore = await auditCounts();
  const supersededDownload = await authenticatedApi.get(
    `${baseUrl}/api/quote-sessions/${sessionA}/download/xlsx`,
  );
  const supersededAuditAfter = await auditCounts();
  if (
    supersededDownload.status() !== 404
    || supersededAuditAfter.version !== supersededAuditBefore.version + 1
    || supersededAuditAfter.artifact !== supersededAuditBefore.artifact
  ) {
    throw new Error("Superseded publication A reached artifact retrieval.");
  }

  const foreignSession = "quote-run560-workspace-b";
  await materialize(
    "/__run560/materialize-cross-workspace",
    foreignSession,
    "run-run560-workspace-b",
    generationPayload(foreignSession),
  );
  const crossAuditBefore = await auditCounts();
  const crossDownload = await authenticatedApi.get(
    `${baseUrl}/api/quote-sessions/${foreignSession}/download/xlsx`,
  );
  const crossAuditAfter = await auditCounts();
  if (
    crossDownload.status() !== 404
    || crossAuditAfter.version !== crossAuditBefore.version
    || crossAuditAfter.artifact !== crossAuditBefore.artifact
  ) {
    throw new Error("Cross-workspace request reached foreign version/artifact retrieval.");
  }

  const anonymousDownload = await anonymousApi.get(
    `${baseUrl}/api/quote-sessions/${sessionA}/download/xlsx`,
  );
  if (anonymousDownload.status() !== 401) {
    throw new Error(`Unauthenticated artifact request was not rejected: ${anonymousDownload.status()}.`);
  }

  const logout = await page.evaluate(async ({ header, token }) => {
    const response = await fetch("/logout", {
      method: "POST",
      headers: { [header]: token },
    });
    return {
      status: response.status,
      location: response.headers.get("X-SQAG-Logout-Location"),
    };
  }, { header: session.body.csrf_header, token: session.body.csrf_token });
  if (logout.status !== 204 || logout.location !== "/signed-out") {
    throw new Error("CSRF-safe logout did not revoke the local session.");
  }

  const revoked = await authenticatedApi.get(`${baseUrl}/api/session`);
  if (revoked.status() !== 401) throw new Error("Logged-out session remained usable.");
  if (consoleProblems.length) throw new Error(`Browser console problems: ${consoleProblems.join(" | ")}`);
  console.log("Run-560 native internal-Google protected export proof passed.");
  console.log("Internal Google synthetic Playwright flow passed.");
} finally {
  if (authenticatedApi) await authenticatedApi.dispose();
  await anonymousApi.dispose();
  await browser.close();
  harness.kill("SIGTERM");
}
