import type { Context, MiddlewareHandler } from "hono";
import { HTTPException } from "hono/http-exception";
import { randomToken, sha256Hex } from "./crypto";
import { SCHEMA_SQL } from "./schema";

export interface Env {
  DB: D1Database;
  CREDENTIAL_KEY?: string;
  REGISTRATION_MODE?: string; // open | first_user_only | closed
  PBKDF2_ITERATIONS?: string;
  /** local development only: allow LAN targets (never set in production) */
  ALLOW_PRIVATE_TARGETS?: string;
}

export interface User {
  id: string;
  email: string;
  display_name: string;
  is_admin: number;
}

export type AppEnv = { Bindings: Env; Variables: { user: User | null; sessionHash: string | null } };
export type Ctx = Context<AppEnv>;

export const now = () => Math.floor(Date.now() / 1000);
export const uuid = () => crypto.randomUUID();
export const COOKIE = "__Host-cs_session";
export const SESSION_TTL = 12 * 3600;

export function fail(status: number, detail: string): never {
  throw new HTTPException(status as 400, { res: Response.json({ detail }, { status }) });
}

export function clientIp(c: Ctx): string {
  return c.req.header("cf-connecting-ip") ?? "unknown";
}

export function iterations(env: Env): number {
  return Math.min(100000, Math.max(10000, parseInt(env.PBKDF2_ITERATIONS ?? "100000", 10) || 100000));
}

export function allowPrivate(env: Env): boolean {
  return env.ALLOW_PRIVATE_TARGETS === "true";
}

// ---------------------------------------------------------------- first-run setup

let schemaReady = false;

/** Creates the tables on first use, so no command line is needed (Cloudflare GitHub integration). */
export async function ensureSchema(env: Env): Promise<void> {
  if (schemaReady) return;
  if (!env.DB) {
    throw new HTTPException(503, { res: Response.json({ detail: "Setup incomplete: in Cloudflare Pages > Settings > Bindings, add a D1 database binding named DB, then redeploy.", setup: "missing_db" }, { status: 503 }) });
  }
  const have = await env.DB.prepare("SELECT name FROM sqlite_master WHERE type = 'table'").all<{ name: string }>();
  const tables = new Set((have.results ?? []).map((r) => r.name));
  const steps: [string, string][] = [["users", SCHEMA_SQL[0]], ["public_cameras_seeded", SCHEMA_SQL[1]], ["app_secrets", SCHEMA_SQL[2]]];
  for (const [marker, sql] of steps) {
    if (marker === "public_cameras_seeded") {
      const n = tables.has("public_cameras") ? (await env.DB.prepare("SELECT COUNT(*) AS n FROM public_cameras WHERE provider = 'seed'").first<{ n: number }>())?.n ?? 0 : 0;
      if (n > 0) continue;
    } else if (tables.has(marker)) continue;
    const stmts = sql.split(/;\s*\n/).map((s) => s.replace(/^\s*--.*$/gm, "").trim()).filter(Boolean);
    await env.DB.batch(stmts.map((s) => env.DB.prepare(s)));
  }
  schemaReady = true;
}

let cachedKey: string | null = null;

/** Credential encryption key: the CREDENTIAL_KEY secret, or (fallback) a random key kept in D1. */
export async function credentialKey(env: Env): Promise<string> {
  if (env.CREDENTIAL_KEY && env.CREDENTIAL_KEY.length >= 32) return env.CREDENTIAL_KEY;
  if (cachedKey) return cachedKey;
  const row = await env.DB.prepare("SELECT value FROM app_secrets WHERE name = 'credential_key'").first<{ value: string }>();
  if (row) return (cachedKey = row.value);
  const k = `d1:${btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32))))}`;
  await env.DB.prepare("INSERT OR IGNORE INTO app_secrets (name, value, created_at) VALUES ('credential_key', ?, ?)").bind(k, now()).run();
  const again = await env.DB.prepare("SELECT value FROM app_secrets WHERE name = 'credential_key'").first<{ value: string }>();
  return (cachedKey = again!.value);
}

// ---------------------------------------------------------------- sessions

function readCookie(c: Ctx): string | null {
  const raw = c.req.header("cookie") ?? "";
  for (const part of raw.split(";")) {
    const [k, ...v] = part.trim().split("=");
    if (k === COOKIE) return v.join("=");
  }
  return null;
}

export async function createSession(c: Ctx, userId: string): Promise<void> {
  const token = randomToken(32);
  const hash = await sha256Hex(token);
  const t = now();
  await c.env.DB.prepare(
    "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, last_seen_at, ip, user_agent) VALUES (?, ?, ?, ?, ?, ?, ?)",
  ).bind(hash, userId, t, t + SESSION_TTL, t, clientIp(c), (c.req.header("user-agent") ?? "").slice(0, 200)).run();
  c.header("Set-Cookie", `${COOKIE}=${token}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${SESSION_TTL}`, { append: true });
}

export async function destroySession(c: Ctx): Promise<void> {
  const h = c.get("sessionHash");
  if (h) await c.env.DB.prepare("DELETE FROM sessions WHERE token_hash = ?").bind(h).run();
  c.header("Set-Cookie", `${COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0`, { append: true });
}

export const sessionMiddleware: MiddlewareHandler<AppEnv> = async (c, next) => {
  c.set("user", null);
  c.set("sessionHash", null);
  const token = readCookie(c);
  if (token && token.length < 100) {
    const hash = await sha256Hex(token);
    const row = await c.env.DB.prepare(
      "SELECT s.expires_at, s.last_seen_at, u.id, u.email, u.display_name, u.is_admin FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
    ).bind(hash).first<{ expires_at: number; last_seen_at: number } & User>();
    if (row && row.expires_at > now()) {
      c.set("user", { id: row.id, email: row.email, display_name: row.display_name, is_admin: row.is_admin });
      c.set("sessionHash", hash);
      // sliding expiry, written at most every 10 minutes to save D1 writes
      if (now() - row.last_seen_at > 600) {
        await c.env.DB.prepare("UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?").bind(now(), now() + SESSION_TTL, hash).run();
      }
    }
  }
  await next();
};

export function requireUser(c: Ctx): User {
  const u = c.get("user");
  if (!u) fail(401, "Authentication required");
  return u;
}

export function requireAdmin(c: Ctx): User {
  const u = requireUser(c);
  if (!u.is_admin) fail(403, "Administrator role required");
  return u;
}

// ---------------------------------------------------------------- CSRF

/** State-changing requests must come from this site (cookies are also SameSite=Strict). */
export const csrfMiddleware: MiddlewareHandler<AppEnv> = async (c, next) => {
  if (!["GET", "HEAD", "OPTIONS"].includes(c.req.method)) {
    const site = c.req.header("sec-fetch-site");
    const origin = c.req.header("origin");
    const self = new URL(c.req.url).origin;
    if (site === "cross-site" || (origin && origin !== self)) {
      return c.json({ detail: "Cross-site request refused" }, 403);
    }
  }
  await next();
};

// ---------------------------------------------------------------- rate limits

/** Fixed-window counter in D1 (shared by all Worker instances). */
export async function rateLimit(env: Env, key: string, limit: number, windowSeconds: number): Promise<void> {
  const t = now();
  const win = t - (t % windowSeconds);
  const row = await env.DB.prepare(
    `INSERT INTO rate_limits (key, window_start, count) VALUES (?, ?, 1)
     ON CONFLICT(key) DO UPDATE SET
       count = CASE WHEN rate_limits.window_start = excluded.window_start THEN rate_limits.count + 1 ELSE 1 END,
       window_start = excluded.window_start
     RETURNING count`,
  ).bind(key, win).first<{ count: number }>();
  if (row && row.count > limit) {
    throw new HTTPException(429, {
      res: Response.json({ detail: "Too many requests, slow down." }, { status: 429, headers: { "Retry-After": String(win + windowSeconds - t) } }),
    });
  }
}

/** Per-isolate limiter for high-frequency, low-risk requests (snapshot refreshes) - saves D1 writes. */
const memHits = new Map<string, number[]>();
export function memLimit(key: string, limit: number, windowMs: number): void {
  const t = Date.now();
  const arr = (memHits.get(key) ?? []).filter((x) => t - x < windowMs);
  if (arr.length >= limit) fail(429, "Too many requests, slow down.");
  arr.push(t);
  memHits.set(key, arr);
}

// ---------------------------------------------------------------- audit

export async function audit(c: Ctx, userId: string | null, action: string, target: string | null = null, detail: Record<string, unknown> = {}) {
  await c.env.DB.prepare("INSERT INTO audit_log (user_id, action, target, ip, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)")
    .bind(userId, action, target, clientIp(c), JSON.stringify(detail), now()).run();
}

// ---------------------------------------------------------------- validation helpers

export async function body<T = Record<string, unknown>>(c: Ctx): Promise<T> {
  try {
    return (await c.req.json()) as T;
  } catch {
    fail(400, "Invalid JSON body");
  }
}

export function str(v: unknown, name: string, { min = 0, max = 200, required = true } = {}): string {
  if (v === undefined || v === null || v === "") {
    if (required && min > 0) fail(422, `${name}: required`);
    return "";
  }
  if (typeof v !== "string") fail(422, `${name}: must be text`);
  const s = v.trim();
  if (s.length < min) fail(422, `${name}: at least ${min} characters`);
  if (s.length > max) fail(422, `${name}: at most ${max} characters`);
  if (/[\u0000-\u001f]/.test(s)) fail(422, `${name}: control characters are not allowed`);
  return s;
}

export function int(v: unknown, name: string, lo: number, hi: number, dflt?: number): number {
  if ((v === undefined || v === null || v === "") && dflt !== undefined) return dflt;
  const n = typeof v === "number" ? v : parseInt(String(v), 10);
  if (!Number.isInteger(n) || n < lo || n > hi) fail(422, `${name}: must be an integer between ${lo} and ${hi}`);
  return n;
}

const HOST_RE = /^(?=.{1,253}$)([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$/;
export function host(v: unknown): string {
  const s = str(v, "host", { min: 1, max: 253 }).toLowerCase().replace(/^\[|\]$/g, "");
  const isV4 = /^\d{1,3}(\.\d{1,3}){3}$/.test(s);
  const isV6 = s.includes(":") && /^[0-9a-f:]+$/.test(s);
  if (!isV4 && !isV6 && !HOST_RE.test(s)) fail(422, "host: enter only a hostname or IP address (no http://, path or credentials)");
  return s;
}

export function path(v: unknown, name: string): string | null {
  if (v === undefined || v === null || v === "") return null;
  let s = str(v, name, { max: 512 });
  if (!s.startsWith("/")) s = "/" + s;
  if (!/^\/[\x21-\x7e]*$/.test(s)) fail(422, `${name}: invalid characters`);
  return s;
}

export function password(v: unknown): string {
  const s = str(v, "password", { min: 12, max: 256 });
  const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((r) => r.test(s)).length;
  if (classes < 3) fail(422, "password: must mix at least 3 of lowercase, uppercase, digits, symbols");
  return s;
}

export function email(v: unknown): string {
  const s = str(v, "email", { min: 3, max: 320 }).toLowerCase();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(s)) fail(422, "email: not a valid address");
  return s;
}
