// Outbound connections to cameras, from Cloudflare's network.
//
// Facts this module is built around (see docs/CLOUD-EDITION-AUDIT.md):
//  * Cloudflare can only reach cameras that are reachable FROM THE INTERNET
//    (public IP / DDNS + router port-forward). LAN addresses never work.
//  * In production, fetch() ignores non-standard ports, so cameras on e.g. :8000
//    are reached with raw TCP sockets (cloudflare:sockets connect()).
//  * Exactly ONE authentication attempt per request: credentials are never retried
//    or varied (no guessing).

import { connect } from "cloudflare:sockets";
import { md5Hex } from "./md5";

export class TargetRejected extends Error {}

const PRIVATE_V4: [number, number][] = [
  [0x0a000000, 8], [0xac100000, 12], [0xc0a80000, 16], // RFC1918
  [0x64400000, 10], // CGNAT
  [0x7f000000, 8], [0xa9fe0000, 16], [0x00000000, 8], // loopback, link-local, "this"
  [0xe0000000, 4], [0xf0000000, 4], // multicast, reserved
];

function v4ToInt(ip: string): number | null {
  const m = ip.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (!m) return null;
  const p = m.slice(1).map(Number);
  if (p.some((x) => x > 255)) return null;
  return ((p[0] << 24) | (p[1] << 16) | (p[2] << 8) | p[3]) >>> 0;
}

/** Explains, in user terms, why an address can never be reached from the cloud. */
export function unreachableReason(host: string): string | null {
  const h = host.trim().toLowerCase().replace(/^\[|\]$/g, "");
  const n = v4ToInt(h);
  if (n !== null) {
    for (const [base, bits] of PRIVATE_V4) {
      const mask = bits === 0 ? 0 : (~0 << (32 - bits)) >>> 0;
      if ((n & mask) >>> 0 === base) {
        return `${h} is a private (LAN) address. Cloudflare can only reach cameras that are reachable from the internet: use your router's public IP or a DDNS name with port-forwarding.`;
      }
    }
    return null;
  }
  if (h.includes(":")) {
    if (h === "::1" || h.startsWith("fe80:") || h.startsWith("fc") || h.startsWith("fd")) {
      return `${h} is a private/local IPv6 address and cannot be reached from the internet.`;
    }
    return null;
  }
  if (h === "localhost" || /\.(local|lan|home|internal|localdomain|home\.arpa)$/.test(h) || !h.includes(".")) {
    return `“${h}” is a local network name. Use a public address or DDNS name that resolves on the internet.`;
  }
  return null;
}

export function assertTarget(host: string, port: number, allowPrivate: boolean): void {
  if (!(port >= 1 && port <= 65535) || port === 25) throw new TargetRejected("Invalid port");
  if (!allowPrivate) {
    const r = unreachableReason(host);
    if (r) throw new TargetRejected(r);
  }
}

// ------------------------------------------------------------------ HTTP client

export interface CamResponse {
  status: number;
  headers: Map<string, string>;
  body: Uint8Array;
  authAttempted: boolean;
}

export interface CamRequest {
  host: string;
  port: number;
  tls: boolean;
  path: string; // starting with "/"
  username?: string;
  password?: string;
  timeoutMs?: number;
  maxBytes?: number;
  allowPrivate?: boolean;
}

const UA = "CameraScope-Free/1.0";

function withTimeout<T>(p: Promise<T>, ms: number, what: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error(`${what} timed out after ${Math.round(ms / 1000)} s`)), ms);
    p.then((v) => { clearTimeout(t); resolve(v); }, (e) => { clearTimeout(t); reject(e); });
  });
}

function parseChallenge(h: string): { scheme: string; params: Record<string, string> } {
  const scheme = h.split(/\s+/, 1)[0].toLowerCase();
  const params: Record<string, string> = {};
  for (const m of h.matchAll(/(\w+)=(?:"([^"]*)"|([^,\s]+))/g)) params[m[1].toLowerCase()] = m[2] ?? m[3];
  return { scheme, params };
}

function authorization(challenge: string, method: string, uri: string, user: string, pass: string): string | null {
  const { scheme, params } = parseChallenge(challenge);
  if (scheme === "basic") return "Basic " + btoa(unescape(encodeURIComponent(`${user}:${pass}`)));
  if (scheme !== "digest" || !params.nonce) return null;
  const realm = params.realm ?? "";
  const alg = (params.algorithm ?? "MD5").toUpperCase();
  if (alg !== "MD5" && alg !== "MD5-SESS") return null;
  const cnonce = crypto.randomUUID().replace(/-/g, "").slice(0, 16);
  const nc = "00000001";
  let ha1 = md5Hex(`${user}:${realm}:${pass}`);
  if (alg === "MD5-SESS") ha1 = md5Hex(`${ha1}:${params.nonce}:${cnonce}`);
  const ha2 = md5Hex(`${method}:${uri}`);
  const qop = (params.qop ?? "").split(",").map((s) => s.trim()).includes("auth") ? "auth" : "";
  const response = qop ? md5Hex(`${ha1}:${params.nonce}:${nc}:${cnonce}:${qop}:${ha2}`) : md5Hex(`${ha1}:${params.nonce}:${ha2}`);
  let h = `Digest username="${user}", realm="${realm}", nonce="${params.nonce}", uri="${uri}", response="${response}", algorithm=${alg}`;
  if (qop) h += `, qop=${qop}, nc=${nc}, cnonce="${cnonce}"`;
  if (params.opaque) h += `, opaque="${params.opaque}"`;
  return h;
}

// --- one raw HTTP/1.1 exchange over a TCP socket (custom ports)
async function rawExchange(req: CamRequest, authHeader: string | null): Promise<CamResponse> {
  const socket = connect({ hostname: req.host, port: req.port }, { secureTransport: req.tls ? "on" : "off", allowHalfOpen: false });
  const limit = req.maxBytes ?? 4 * 1024 * 1024;
  try {
    await withTimeout(socket.opened, req.timeoutMs ?? 8000, `Connection to ${req.host}:${req.port}`);
    const hostHdr = req.port === (req.tls ? 443 : 80) ? req.host : `${req.host}:${req.port}`;
    let head = `GET ${req.path} HTTP/1.1\r\nHost: ${hostHdr}\r\nUser-Agent: ${UA}\r\nAccept: */*\r\nConnection: close\r\n`;
    if (authHeader) head += `Authorization: ${authHeader}\r\n`;
    const w = socket.writable.getWriter();
    await w.write(new TextEncoder().encode(head + "\r\n"));
    w.releaseLock();
    const reader = socket.readable.getReader();
    const chunks: Uint8Array[] = [];
    let total = 0;
    const readAll = (async () => {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        chunks.push(value);
        total += value.length;
        if (total > limit + 65536) throw new Error("Camera response too large");
      }
    })();
    await withTimeout(readAll, req.timeoutMs ?? 8000, "Camera response");
    const all = new Uint8Array(total);
    let o = 0;
    for (const c of chunks) { all.set(c, o); o += c.length; }
    return parseHttp(all, limit);
  } finally {
    try { await socket.close(); } catch { /* already closed */ }
  }
}

function parseHttp(all: Uint8Array, limit: number): CamResponse {
  let i = 0;
  for (; i < all.length - 3; i++) if (all[i] === 13 && all[i + 1] === 10 && all[i + 2] === 13 && all[i + 3] === 10) break;
  if (i >= all.length - 3) throw new Error("Not an HTTP response");
  const head = new TextDecoder().decode(all.subarray(0, i)).split("\r\n");
  const m = head[0].match(/^HTTP\/1\.[01] (\d{3})/);
  if (!m) throw new Error("Not an HTTP response");
  const headers = new Map<string, string>();
  for (const l of head.slice(1)) {
    const k = l.indexOf(":");
    if (k > 0) {
      const name = l.slice(0, k).trim().toLowerCase();
      if (!headers.has(name)) headers.set(name, l.slice(k + 1).trim());
    }
  }
  let body = all.subarray(i + 4);
  if ((headers.get("transfer-encoding") ?? "").toLowerCase().includes("chunked")) {
    const parts: Uint8Array[] = [];
    let p = 0, size = 0;
    while (p < body.length) {
      let e = p;
      while (e < body.length - 1 && !(body[e] === 13 && body[e + 1] === 10)) e++;
      const n = parseInt(new TextDecoder().decode(body.subarray(p, e)).split(";")[0], 16);
      if (!n) break;
      parts.push(body.subarray(e + 2, e + 2 + n));
      size += n;
      p = e + 2 + n + 2;
    }
    const merged = new Uint8Array(size);
    let o = 0;
    for (const c of parts) { merged.set(c, o); o += c.length; }
    body = merged;
  } else if (headers.has("content-length")) {
    body = body.subarray(0, parseInt(headers.get("content-length")!, 10));
  }
  if (body.length > limit) throw new Error("Camera response too large");
  return { status: parseInt(m[1], 10), headers, body, authAttempted: false };
}

// --- one exchange with fetch() (standard ports 80/443)
async function fetchExchange(req: CamRequest, authHeader: string | null): Promise<CamResponse> {
  const url = `${req.tls ? "https" : "http"}://${req.host.includes(":") ? `[${req.host}]` : req.host}${req.path}`;
  const headers: Record<string, string> = { "User-Agent": UA, Accept: "*/*" };
  if (authHeader) headers.Authorization = authHeader;
  const r = await withTimeout(fetch(url, { headers, redirect: "manual" }), req.timeoutMs ?? 8000, `Request to ${req.host}`);
  const limit = req.maxBytes ?? 4 * 1024 * 1024;
  const buf = new Uint8Array(await withTimeout(r.arrayBuffer(), req.timeoutMs ?? 8000, "Camera response"));
  if (buf.length > limit) throw new Error("Camera response too large");
  const h = new Map<string, string>();
  r.headers.forEach((v, k) => h.set(k.toLowerCase(), v));
  return { status: r.status, headers: h, body: buf, authAttempted: false };
}

/** GET a path on a camera, answering ONE auth challenge with the owner's credentials. */
export async function cameraGet(req: CamRequest): Promise<CamResponse> {
  assertTarget(req.host, req.port, !!req.allowPrivate);
  const standard = req.port === (req.tls ? 443 : 80);
  const exchange = standard ? fetchExchange : rawExchange;
  const first = await exchange(req, null);
  if (first.status !== 401 || !req.username) return first;
  const challenge = first.headers.get("www-authenticate") ?? "";
  const auth = authorization(challenge, "GET", req.path, req.username, req.password ?? "");
  if (!auth) return first;
  const second = await exchange(req, auth);
  second.authAttempted = true;
  return second;
}

/** Credential-free TCP reachability check. */
export async function tcpCheck(host: string, port: number, tls: boolean, allowPrivate: boolean, timeoutMs = 5000): Promise<void> {
  assertTarget(host, port, allowPrivate);
  if (port === 80 || port === 443) {
    // Workers route standard ports through fetch(); any HTTP answer proves reachability.
    await withTimeout(fetch(`${port === 443 ? "https" : "http"}://${host}/`, { redirect: "manual" }), timeoutMs, `Connection to ${host}:${port}`);
    return;
  }
  const s = connect({ hostname: host, port }, { secureTransport: tls ? "on" : "off", allowHalfOpen: false });
  try {
    await withTimeout(s.opened, timeoutMs, `Connection to ${host}:${port}`);
  } finally {
    try { await s.close(); } catch { /* ignore */ }
  }
}

export function isImage(r: CamResponse): string | null {
  const ct = (r.headers.get("content-type") ?? "").split(";")[0].trim().toLowerCase();
  if (ct.startsWith("image/")) return ct;
  // some firmwares send application/octet-stream for JPEGs: check the JPEG magic number
  if (r.body.length > 3 && r.body[0] === 0xff && r.body[1] === 0xd8 && r.body[2] === 0xff) return "image/jpeg";
  return null;
}
