// Synthetic browser fixture only. Production never supplies credentials through this module.
export async function signIn(page, baseUrl, alias = "alpha-admin" + "@" + "example.test", password = "synthetic-browser-group-password") {
  await page.goto(baseUrl + "/login", { waitUntil: "domcontentloaded" });
  await page.getByLabel("Email", { exact: true }).fill(alias);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await Promise.all([
    page.waitForURL(baseUrl + "/"),
    page.getByRole("button", { name: "Sign in", exact: true }).click(),
  ]);
}

// Only public, authenticated authority participates in browser continuity checks.
export function browserAuthority(session, profiles) {
  const scope = session?.browser_recovery_scope;
  const workspace = profiles?.workspace?.workspace?.id;
  if (session?.authenticated !== true || session.user !== null
      || typeof scope !== "string" || !scope || scope !== scope.trim()
      || typeof workspace !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$/.test(workspace)
      || session.permissions?.canGenerateQuote !== true) {
    throw new Error("Authenticated browser authority is incomplete or exposed.");
  }
  return { scope, workspace };
}

export function assertBrowserAuthorityUnchanged(before, after) {
  if (typeof before?.scope !== "string" || !before.scope
      || typeof before?.workspace !== "string" || !before.workspace
      || before.scope !== after?.scope || before.workspace !== after?.workspace) {
    throw new Error("Authenticated authority changed after fresh quote creation.");
  }
}
