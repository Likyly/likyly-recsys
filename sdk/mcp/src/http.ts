#!/usr/bin/env node
// Hosted MCP service: the same tools as the local stdio server, exposed over MCP's
// Streamable HTTP transport so any MCP-capable LLM client can connect by URL
// (e.g. https://mcp.likyly.com/mcp) instead of running a local process.
//
// Stateless and multi-tenant: every request carries the caller's own *restricted public*
// key (Authorization: Bearer <key>, or X-Likyly-Api-Key). The service stores no secrets and
// simply forwards that key to the LIKYLY API, so a caller can never do more through MCP
// than they could with the key directly.
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { LikylyClient } from "./client.js";
import { createLikylyServer } from "./tools.js";

const port = Number(process.env.PORT ?? 3200);
const baseUrl = process.env.LIKYLY_BASE_URL ?? "https://api.likyly.com";
const MAX_BODY_BYTES = 1_000_000;

function setCors(res: ServerResponse) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader(
    "Access-Control-Allow-Headers",
    "Authorization, Content-Type, X-Likyly-Api-Key, X-Likyly-Product-Type, Mcp-Session-Id, Mcp-Protocol-Version",
  );
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Expose-Headers", "Mcp-Session-Id");
}

function sendJson(res: ServerResponse, status: number, body: unknown) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

function jsonRpcError(res: ServerResponse, status: number, code: number, message: string) {
  sendJson(res, status, { jsonrpc: "2.0", error: { code, message }, id: null });
}

function extractApiKey(req: IncomingMessage): string | undefined {
  const auth = req.headers.authorization;
  if (auth?.toLowerCase().startsWith("bearer ")) return auth.slice(7).trim() || undefined;
  const header = req.headers["x-likyly-api-key"];
  return (Array.isArray(header) ? header[0] : header)?.trim() || undefined;
}

async function readJson(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += (chunk as Buffer).length;
    if (size > MAX_BODY_BYTES) throw new Error("Request body too large");
    chunks.push(chunk as Buffer);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

const server = createServer(async (req, res) => {
  setCors(res);
  const path = (req.url ?? "/").split("?")[0];

  if (req.method === "OPTIONS") {
    res.writeHead(204).end();
    return;
  }
  if (path === "/health") {
    sendJson(res, 200, { status: "ok", service: "likyly-mcp" });
    return;
  }
  if (path !== "/mcp") {
    sendJson(res, 404, { error: "Not found. The MCP endpoint is /mcp." });
    return;
  }
  // Stateless mode: no server-initiated streams or sessions to resume.
  if (req.method !== "POST") {
    res.setHeader("Allow", "POST, OPTIONS");
    jsonRpcError(res, 405, -32000, "Method not allowed - use POST.");
    return;
  }

  const apiKey = extractApiKey(req);
  if (!apiKey) {
    res.setHeader("WWW-Authenticate", 'Bearer realm="likyly-mcp"');
    jsonRpcError(res, 401, -32001, "Missing API key. Send your restricted public key as 'Authorization: Bearer <key>'.");
    return;
  }

  let body: unknown;
  try {
    body = await readJson(req);
  } catch {
    jsonRpcError(res, 400, -32700, "Invalid or oversized JSON body.");
    return;
  }

  const productHeader = req.headers["x-likyly-product-type"];
  const defaultProductType = (Array.isArray(productHeader) ? productHeader[0] : productHeader)?.trim() || undefined;

  const mcp = createLikylyServer(new LikylyClient({ baseUrl, apiKey }), defaultProductType);
  const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
  res.on("close", () => {
    void transport.close();
    void mcp.close();
  });
  try {
    await mcp.connect(transport);
    await transport.handleRequest(req, res, body);
  } catch (err) {
    console.error("MCP request failed:", err);
    if (!res.headersSent) jsonRpcError(res, 500, -32603, "Internal server error.");
  }
});

server.listen(port, () => {
  console.error(`likyly-mcp HTTP service listening on :${port} (POST /mcp) -> ${baseUrl}`);
});
