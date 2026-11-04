// Browser sign-in: the wizard listens on 127.0.0.1 (random port), opens likyly.com/cli/auth,
// and the developer signs in or creates an account there. The page then POSTs the public key
// back to this local server (a top-level form submit: the key never goes in a URL), guarded by
// a one-time `state` so no other page can push a key into the wizard.

import { createServer } from "node:http";
import { randomBytes, timingSafeEqual } from "node:crypto";
import { basename } from "node:path";
import { isServerOnlyKey } from "./setup.mjs";

export const APP_URL = (process.env.LIKYLY_APP_URL || "https://www.likyly.com").replace(/\/+$/, "");
const TIMEOUT_MS = 15 * 60 * 1000;

const page = (title, text, ok = true) => `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>${title} · LIKYLY wizard</title>
<style>
  :root{color-scheme:light dark}
  body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b1020;color:#e8ecf4;
       font:16px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
  main{max-width:30rem;padding:2.5rem;border:1px solid #2a3554;border-radius:20px;background:#111a33;text-align:center}
  .mark{font:700 1.6rem/1 ui-monospace,Menlo,monospace;letter-spacing:.35em;
        background:linear-gradient(90deg,#2F6BFF,#7758F6);-webkit-background-clip:text;color:transparent}
  .bar{display:flex;justify-content:center;gap:4px;margin:12px 0 28px}.bar i{width:22px;height:4px;border-radius:2px}
  h1{font-size:1.35rem;margin:0 0 .5rem;color:${ok ? "#20C6A5" : "#FF5B72"}}p{margin:0;color:#b6c0d6}
</style></head><body><main>
<div class="mark">LIKYLY</div>
<div class="bar"><i style="background:#F6A92A"></i><i style="background:#FF5B72"></i><i style="background:#2F6BFF"></i><i style="background:#20C6A5"></i></div>
<h1>${title}</h1><p>${text}</p></main></body></html>`;

function sameSecret(a, b) {
  const [x, y] = [Buffer.from(String(a)), Buffer.from(String(b))];
  return x.length === y.length && timingSafeEqual(x, y);
}

function readBody(req, limit = 8192) {
  return new Promise((resolve, reject) => {
    let body = "";
    req.on("data", (c) => {
      body += c;
      if (body.length > limit) reject(new Error("body too large"));
    });
    req.on("end", () => resolve(body));
    req.on("error", reject);
  });
}

/**
 * Start listening and build the sign-in URL. `result` resolves to
 * `{ key, devKey, workspace }` (keys received), `{ manual: true, devKey }` (the developer chose
 * to paste the public key)
 * or `{ timeout: true }`.
 */
export async function startBrowserAuth({ project, dir }) {
  const state = randomBytes(16).toString("hex");
  let settle;
  const result = new Promise((r) => (settle = r));

  const server = createServer(async (req, res) => {
    if (req.method !== "POST" || !req.url.startsWith("/callback")) {
      res.writeHead(404).end();
      return;
    }
    let form;
    try {
      form = new URLSearchParams(await readBody(req));
    } catch {
      res.writeHead(413).end();
      return;
    }
    const html = (status, ...args) => res.writeHead(status, { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" }).end(page(...args));
    if (!sameSecret(form.get("state") ?? "", state)) {
      html(400, "This link has expired", "Run <code>npx @likyly/wizard</code> again from your project.", false);
      return;
    }
    // The developer key (for the MCP server) comes along with either answer.
    const dev = (form.get("dev_key") ?? "").trim();
    const devKey = dev.startsWith("lk_") ? dev : null;
    if (form.get("manual") === "1") {
      html(200, "Back to your terminal", "Paste your public key in the wizard.");
      settle({ manual: true, devKey });
      return;
    }
    const key = (form.get("key") ?? "").trim();
    if (!key.startsWith("pk_") || isServerOnlyKey(key)) {
      html(400, "That is not a public key", "Run <code>npx @likyly/wizard</code> again from your project.", false);
      return;
    }
    html(200, "You're connected", "The wizard received your public key. You can close this tab and go back to your terminal.");
    settle({ key, devKey, workspace: form.get("workspace") || null });
  });

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const timer = setTimeout(() => settle({ timeout: true }), TIMEOUT_MS);
  timer.unref();

  const params = new URLSearchParams({ port: String(server.address().port), state });
  if (project?.label) params.set("framework", project.label);
  if (dir) params.set("project", basename(dir));
  return {
    url: `${APP_URL}/cli/auth?${params}`,
    result,
    close() {
      clearTimeout(timer);
      server.closeAllConnections?.();
      server.close();
    },
  };
}
