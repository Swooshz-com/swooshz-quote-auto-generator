import fs from "node:fs/promises";
import path from "node:path";

export async function freshBrowserRun(parent, prefix) {
  await fs.mkdir(parent, { recursive: true });
  return fs.mkdtemp(path.join(parent, prefix));
}

// Own every context's admission policy before it creates a page or sends HTTP.
export class SyntheticBrowserBoundary {
  constructor(origin) {
    const target = new URL(origin);
    if (target.protocol !== "http:" || !["127.0.0.1", "[::1]"].includes(target.hostname)
        || target.username || target.password || target.pathname !== "/" || target.search || target.hash) {
      throw new Error("Synthetic browser requires its owned loopback origin.");
    }
    this.origin = target.origin;
    this.unexpectedRequests = 0;
  }

  admit(value) {
    try {
      const target = new URL(value, this.origin);
      if (target.origin === this.origin && !target.username && !target.password) return true;
    } catch {}
    this.unexpectedRequests += 1;
    return false;
  }

  assertClean() {
    if (this.unexpectedRequests) throw new Error("Synthetic browser attempted a request outside its owned origin.");
  }

  async newContext(browser, options = {}) {
    const context = await browser.newContext({ ...options, serviceWorkers: "block" });
    await context.route("**/*", async (route) => {
      if (!this.admit(route.request().url())) return route.abort("blockedbyclient");
      return route.fallback();
    });
    await context.routeWebSocket("**/*", (socket) => {
      this.unexpectedRequests += 1;
      socket.close({ code: 1008, reason: "Unexpected synthetic browser WebSocket" });
    });
    return context;
  }

  async get(api, value) {
    if (!this.admit(value)) {
      this.assertClean();
    }
    const response = await api.get(value, { maxRedirects: 0 });
    if (response.status() >= 300 && response.status() < 400) {
      throw new Error("Synthetic API redirects are forbidden.");
    }
    return response;
  }
}
