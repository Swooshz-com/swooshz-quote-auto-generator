import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { chromium, request } from "playwright";
import { signIn } from "./internal-password-browser-test.mjs";

const harness = spawn(process.env.PYTHON || "python", ["tests/internal_password_browser_harness.py"], {
  cwd: process.cwd(), env: { ...process.env, PYTHONUNBUFFERED: "1", SQAG_DISABLE_DOTENV: "1" },
  stdio: ["ignore", "pipe", "pipe"], windowsHide: true,
});
harness.stderr.resume();
let browser;
try {
  const base = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Synthetic password harness readiness timeout.")), 15000);
    harness.stdout.once("data", chunk => { clearTimeout(timer); resolve(chunk.toString().trim().split(/\r?\n/)[0]); });
    harness.once("exit", code => { clearTimeout(timer); reject(new Error(`Synthetic password harness exited (${code}).`)); });
  });
  assert.equal(new URL(base).hostname, "127.0.0.1");
  const channel = process.env.CI ? undefined : "chrome";
  browser = await chromium.launch({ headless: true, ...(channel ? { channel } : {}) });
  const context = await browser.newContext();
  const external = [];
  await context.route("**/*", route => {
    if (new URL(route.request().url()).origin === base) return route.fallback();
    external.push("external request blocked"); return route.abort("blockedbyclient");
  });
  const page = await context.newPage();
  await page.route("**/api/quote-sessions", route => route.fulfill({status:200, contentType:"application/json", body:'{"quote_sessions":[]}'}));
  await page.route("**/api/log", route => route.fulfill({status:204,body:""}));
  assert.equal((await context.request.get(base+"/api/session")).status(),401);
  async function assertAuthLayout(title, action) {
    await page.getByRole("heading", { name: title, exact: true }).waitFor();
    await page.locator(".auth-brand").getByText("Swooshz Quote Generator").waitFor();
    assert.equal(await page.getByRole("link", { name: "Privacy Notice" }).getAttribute("href"), "/privacy");
    const panel = await page.locator(".auth-panel").boundingBox();
    const viewport = page.viewportSize();
    assert.ok(panel && panel.x >= 0 && panel.x + panel.width <= viewport.width);
    assert.ok(await action.evaluate(element => element.getBoundingClientRect().height >= 44));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), viewport.width);
  }
  for (const [width, height, alias, password] of [
    [1280, 800, "unknown" + "@" + "example.test", "synthetic-browser-admin-password"],
    [375, 667, "alpha-admin" + "@" + "example.test", "synthetic-wrong-password"],
  ]) {
    await page.setViewportSize({ width, height });
    await page.goto(base + "/login");
    await assertAuthLayout("Staff sign in", page.getByRole("button", { name: "Sign in", exact: true }));
    await page.keyboard.press("Tab");
    assert.equal(await page.locator(":focus").getAttribute("name"), "email");
    await page.getByLabel("Email",{exact:true}).fill(alias);
    await page.getByLabel("Password",{exact:true}).fill(password);
    const failed=page.waitForResponse(r=>r.url()===base+"/login"&&r.request().method()==="POST");
    await page.getByRole("button",{name:"Sign in",exact:true}).click();
    assert.equal((await failed).status(),403);
    const retry = page.getByRole("link", { name: "Try again", exact: true });
    await assertAuthLayout("Could not sign in", retry);
    assert.match(await page.locator("#authPageMessage").innerText(), /support with reference /);
    assert.equal(await retry.getAttribute("href"), "/login");
    assert.equal((await context.request.get(base+"/api/session")).status(),401);
  }
  await page.setViewportSize({ width: 1280, height: 800 });
  await signIn(page,base);
  const session=await (await context.request.get(base+"/api/session")).json();
  assert.equal(session.authenticated,true); assert.equal(session.user,null); assert.ok(session.browser_recovery_scope);
  const copied=await request.newContext({storageState:await context.storageState()});
  assert.equal((await copied.get(base+"/logout")).status(),405);
  assert.equal((await copied.post(base+"/logout",{headers:{Origin:base}})).status(),403);
  await page.waitForFunction(()=>typeof state!=="undefined"&&!state.isBooting);
  const logout=page.waitForResponse(r=>r.url()===base+"/logout"&&r.request().method()==="POST");
  await page.locator("#topbarLogoutLink").click();
  assert.equal((await logout).status(),204); await page.waitForURL(base+"/signed-out");
  assert.equal((await copied.get(base+"/api/session")).status(),401);
  for(let i=0;i<2;i++) {
    assert.equal((await page.goto(base+"/")).status(),401);
    assert.equal(await page.locator("#topbarLogoutLink").count(),0);
  }
  await signIn(page,base,"alpha-operator"+"@"+"example.test","synthetic-browser-operator-password");
  const selected=await (await context.request.get(base+"/api/session")).json();
  assert.equal(selected.authenticated,true); assert.notEqual(selected.browser_recovery_scope,session.browser_recovery_scope);
  assert.equal((await copied.get(base+"/api/session")).status(),401);
  assert.deepEqual(external,[]);
  await copied.dispose(); await context.close();
  console.log(JSON.stringify({status:"passed",browser:channel||"chromium",real_provider_calls:0}));
} finally {
  if(browser) await browser.close();
  if(harness.exitCode===null) { const exited=new Promise(resolve=>harness.once("exit",resolve)); harness.kill(); await exited; }
}
