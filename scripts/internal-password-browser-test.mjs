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
