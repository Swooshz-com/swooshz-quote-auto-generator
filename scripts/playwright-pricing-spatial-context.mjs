// Focused extension of the existing synthetic owner-UAT carrier. No live provider is used.
// Anchor checks fail closed if the shared carrier changes instead of silently skipping coverage.
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
const root = fileURLToPath(new URL('..', import.meta.url));
const evidence = process.env.SQAG_OWNER_UAT_EVIDENCE_ROOT || path.join(root, '_logs/browser/pricing-spatial-context');
await fs.mkdir(evidence, { recursive: true });
let code = await fs.readFile(path.join(root, 'scripts/playwright-owner-uat-basis-repair.mjs'), 'utf8');
function replaceOnce(before, after) {
  if (code.split(before).length !== 2) throw new Error('Shared pricing carrier anchor changed: ' + before.slice(0, 90));
  code = code.replace(before, after);
}
replaceOnce('const sourceRoot = fileURLToPath(new URL("..", import.meta.url));', 'const sourceRoot = process.cwd();');
replaceOnce('import path from "node:path";', 'import path from "node:path";\nimport os from "node:os";');
replaceOnce('const dirs = Object.fromEntries(["data", "output", "temp", "pricing", "profiles", "logs", "downloads"].map((name) => [name, path.join(runRoot, name)]));', `
const nativeRunRoot = await fs.mkdtemp(path.join(os.tmpdir(), "sqag-spatial-"));
const dirs = Object.fromEntries(["data", "output", "temp", "pricing", "profiles", "logs", "downloads"].map((name) => [name,
  path.join(["logs", "downloads"].includes(name) ? runRoot : nativeRunRoot, name)]));`);
replaceOnce('Bar Stool seating around the display', 'Bar Stools at Round Tables.');
replaceOnce('  // U2: Confirm Quotation Basis must show its preparing state before slow network work completes.', `
  const beforeConfirm = await page.evaluate(() => {
    const row = state.lineItems.find((entry) => entry.source_basis_line_id === "u1-count");
    return { variant: row?.pricing_authority?.variant, price: pricingAuthorityPrice(row?.pricing_authority) };
  });
  assert(!["catalog", "manual", "included"].includes(beforeConfirm.variant) && beforeConfirm.price === null,
    "candidate selection became quoted pricing before Confirm", beforeConfirm);
  result.spatialBeforeConfirm = beforeConfirm;
  // U2: Confirm Quotation Basis must show its preparing state before slow network work completes.`);
replaceOnce('    await writeJson("u1-pricing.json", pricing);', `
    const refusalReview = await page.evaluate(() => ({
      issues: state.pricingIssues,
      errors: outputRowsValid().errors,
      visible: document.querySelector("#pricingReviewMessages").innerText,
    }));
    assert(refusalReview.issues.some((issue) => issue.source_basis_line_id === "u1-video" && issue.reason === "unit_review_required"),
      "HTTP 200 partial refusal was not retained as a finite row issue", refusalReview);
    assert(refusalReview.errors.some((error) => error.includes("different quantity unit")) && refusalReview.visible.includes("different quantity unit"),
      "unit refusal reason was not rendered in the existing review area", refusalReview);
    await page.screenshot({ path: path.join(runRoot, "pricing-refusal.png"), fullPage: true });
    const finiteControls = await page.evaluate(() => {
      const row = state.outputRows.find((entry) => entry.source_basis_line_id === "u1-video");
      const duplicate = pricingSelectionReviewMessage(row, [row, { ...row }]);
      const saved = state.pricingIssues;
      state.pricingIssues = [{ source_basis_line_id: row.source_basis_line_id, reason: "<script>untrusted</script>" }];
      const unknown = pricingSelectionReviewMessage(row, [row]);
      state.pricingIssues = saved;
      return { duplicate, unknown };
    });
    assert(finiteControls.duplicate === "" && finiteControls.unknown === "", "ambiguous ids or unknown reasons escaped the finite wording map", finiteControls);
    const video = await rowFor("u1-video");
    await page.locator('[data-output-row="' + video.index + '"][data-output-edit-field="unit_price_override"][data-output-denomination="quote"]').first().click();
    await page.locator('[data-output-editor-field="unit_price_override"]').fill("12");
    await page.locator('[data-output-editor-field="unit_price_override"]').press("Enter");
    const resolvedVideo = await rowFor("u1-video");
    assert(resolvedVideo.authority?.variant === "manual" && resolvedVideo.quoted === "12.00",
      "manual review price did not resolve refused selection", resolvedVideo);
    assert(!(await page.evaluate(() => outputRowsValid().errors)).some((error) => error.includes("different quantity unit")),
      "resolved manual price retained a stale unit refusal");
    result.spatialPartialRefusal = { refusalReview, resolvedVideo };
    await writeJson("u1-pricing.json", pricing);`);
const runtime = path.join(evidence, 'pricing-spatial-carrier.mjs');
await fs.writeFile(runtime, code);
const child = spawn(process.execPath, [runtime, '--lines=0', '--latency-ms=100', '--fresh-analysis', ...process.argv.slice(2)], {
  cwd: root, windowsHide: true, stdio: 'inherit',
  env: { ...process.env, SQAG_OWNER_UAT_EVIDENCE_ROOT: evidence },
});
const exitCode = await new Promise((resolve) => child.once('exit', resolve));
process.exitCode = exitCode ?? 1;
