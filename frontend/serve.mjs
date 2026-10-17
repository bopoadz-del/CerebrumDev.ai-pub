#!/usr/bin/env node
/**
 * Production static server for the Factory Floor SPA.
 *
 * Render's live frontend (srv-d9ta36v40ujc73dsmhkg) is a static_site.
 * That runtime ignores frontend/public/_headers (a Netlify/Pages file) and
 * never applied the Blueprint `headers:` block, so curl against
 * www.cerebrum-dev.com returned only nosniff. This process is the serving
 * path that actually stamps the five headers the post-deploy smoke reads
 * off the wire. Bind 0.0.0.0:$PORT (Render).
 */
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(process.env.STATIC_ROOT || path.join(HERE, "dist"));
const HOST = process.env.HOST || "0.0.0.0";
const PORT = Number(process.env.PORT || 8080);

const SECURITY_HEADERS = JSON.parse(
  fs.readFileSync(path.join(HERE, "security-headers.json"), "utf8"),
);

const MIME = {
  ".css": "text/css; charset=utf-8",
  ".gif": "image/gif",
  ".html": "text/html; charset=utf-8",
  ".ico": "image/x-icon",
  ".jpeg": "image/jpeg",
  ".jpg": "image/jpeg",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".map": "application/json",
  ".mjs": "text/javascript; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".txt": "text/plain; charset=utf-8",
  ".webmanifest": "application/manifest+json",
  ".webp": "image/webp",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".xml": "application/xml",
};

function safeJoin(root, urlPath) {
  const decoded = decodeURIComponent((urlPath || "/").split("?")[0].split("#")[0]);
  const rel = decoded.replace(/^\/+/, "");
  const resolved = path.resolve(root, rel);
  const rootWithSep = root.endsWith(path.sep) ? root : root + path.sep;
  if (resolved !== root && !resolved.startsWith(rootWithSep)) {
    return null;
  }
  return resolved;
}

function resolveFile(urlPath) {
  const candidate = safeJoin(ROOT, urlPath);
  if (!candidate) {
    return { status: 403, file: null };
  }
  try {
    const st = fs.statSync(candidate);
    if (st.isFile()) {
      return { status: 200, file: candidate };
    }
    if (st.isDirectory()) {
      const index = path.join(candidate, "index.html");
      if (fs.existsSync(index) && fs.statSync(index).isFile()) {
        return { status: 200, file: index };
      }
    }
  } catch {
    // missing path — SPA fallback or 404 below
  }
  const ext = path.extname((urlPath || "/").split("?")[0]);
  if (!ext) {
    const spa = path.join(ROOT, "index.html");
    if (fs.existsSync(spa)) {
      return { status: 200, file: spa };
    }
  }
  return { status: 404, file: null };
}

function onRequest(req, res) {
  if (req.method !== "GET" && req.method !== "HEAD") {
    res.writeHead(405, { Allow: "GET, HEAD", ...SECURITY_HEADERS });
    res.end();
    return;
  }
  const { status, file } = resolveFile(req.url || "/");
  const headers = { ...SECURITY_HEADERS };
  if (file) {
    headers["Content-Type"] =
      MIME[path.extname(file).toLowerCase()] || "application/octet-stream";
    const body = fs.readFileSync(file);
    headers["Content-Length"] = String(body.length);
    res.writeHead(status, headers);
    res.end(req.method === "HEAD" ? undefined : body);
    return;
  }
  res.writeHead(status, headers);
  res.end();
}

const server = http.createServer(onRequest);
server.listen(PORT, HOST, () => {
  console.log(`cerebrumdev-frontend listening on ${HOST}:${PORT} root=${ROOT}`);
});
