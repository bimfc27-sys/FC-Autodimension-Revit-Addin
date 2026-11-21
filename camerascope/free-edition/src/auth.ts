import { Hono } from "hono";
import { hashPassword, verifyPassword } from "./crypto";
import {
  type AppEnv, audit, body, clientIp, createSession, destroySession, email, fail, iterations, now, password,
  rateLimit, requireAdmin, requireUser, str, uuid,
} from "./core";

const MAX_FAILED = 5;
const LOCK_SECONDS = 15 * 60;

export const auth = new Hono<AppEnv>();

const out = (u: { id: string; email: string; display_name: string; is_admin: number }) => ({
  id: u.id, email: u.email, display_name: u.display_name, is_admin: !!u.is_admin,
});

async function userCount(db: D1Database): Promise<number> {
  return (await db.prepare("SELECT COUNT(*) AS n FROM users").first<{ n: number }>())?.n ?? 0;
}

auth.get("/registration", async (c) => {
  const first = (await userCount(c.env.DB)) === 0;
  return c.json({ open: first || c.env.REGISTRATION_MODE === "open", first_user: first });
});

auth.post("/register", async (c) => {
  await rateLimit(c.env, `register:${clientIp(c)}`, 5, 3600);
  const b = await body(c);
  const em = email(b.email), pw = password(b.password), name = str(b.display_name, "display_name", { min: 1, max: 120 });
  const first = (await userCount(c.env.DB)) === 0;
  if (!first && c.env.REGISTRATION_MODE !== "open") fail(403, "Registration is closed. Ask the administrator for an account.");
  if (await c.env.DB.prepare("SELECT 1 FROM users WHERE email = ?").bind(em).first()) fail(409, "An account with this e-mail already exists");
  const id = uuid();
  await c.env.DB.prepare("INSERT INTO users (id, email, password_hash, display_name, is_admin, created_at) VALUES (?, ?, ?, ?, ?, ?)")
    .bind(id, em, await hashPassword(pw, iterations(c.env)), name, first ? 1 : 0, now()).run();
  await audit(c, id, "auth.register", em, { admin: first });
  await createSession(c, id);
  return c.json(out({ id, email: em, display_name: name, is_admin: first ? 1 : 0 }), 201);
});

auth.post("/login", async (c) => {
  const b = await body(c);
  const em = email(b.email);
  const pw = typeof b.password === "string" ? b.password.slice(0, 256) : "";
  await rateLimit(c.env, `login-ip:${clientIp(c)}`, 20, 300);
  await rateLimit(c.env, `login-email:${em}`, 10, 300);
  const u = await c.env.DB.prepare("SELECT * FROM users WHERE email = ?").bind(em)
    .first<{ id: string; email: string; display_name: string; is_admin: number; password_hash: string; failed_logins: number; locked_until: number | null }>();
  const t = now();
  if (u && u.locked_until && u.locked_until > t) {
    await verifyPassword(pw, null, iterations(c.env)); // same timing, same message
    fail(401, "Invalid e-mail or password");
  }
  if (!(await verifyPassword(pw, u?.password_hash ?? null, iterations(c.env)))) {
    if (u) {
      const n = u.failed_logins + 1;
      const lock = n >= MAX_FAILED;
      await c.env.DB.prepare("UPDATE users SET failed_logins = ?, locked_until = ? WHERE id = ?")
        .bind(lock ? 0 : n, lock ? t + LOCK_SECONDS : u.locked_until, u.id).run();
      await audit(c, u.id, lock ? "auth.lockout" : "auth.login_failed", em);
    }
    fail(401, "Invalid e-mail or password");
  }
  await c.env.DB.prepare("UPDATE users SET failed_logins = 0, locked_until = NULL WHERE id = ?").bind(u!.id).run();
  await audit(c, u!.id, "auth.login", em);
  await createSession(c, u!.id);
  return c.json(out(u!));
});

auth.post("/logout", async (c) => {
  await destroySession(c);
  return c.body(null, 204);
});

auth.post("/logout-all", async (c) => {
  const u = requireUser(c);
  await c.env.DB.prepare("DELETE FROM sessions WHERE user_id = ?").bind(u.id).run();
  await audit(c, u.id, "auth.logout_all");
  await destroySession(c);
  return c.body(null, 204);
});

auth.get("/me", (c) => c.json(out(requireUser(c))));

auth.get("/users", async (c) => {
  requireAdmin(c);
  const r = await c.env.DB.prepare("SELECT id, email, display_name, is_admin FROM users ORDER BY created_at").all();
  return c.json((r.results as never[]).map(out));
});

auth.post("/users", async (c) => {
  const admin = requireAdmin(c);
  const b = await body(c);
  const em = email(b.email), pw = password(b.password), name = str(b.display_name, "display_name", { min: 1, max: 120 });
  if (await c.env.DB.prepare("SELECT 1 FROM users WHERE email = ?").bind(em).first()) fail(409, "An account with this e-mail already exists");
  const id = uuid();
  await c.env.DB.prepare("INSERT INTO users (id, email, password_hash, display_name, is_admin, created_at) VALUES (?, ?, ?, ?, ?, ?)")
    .bind(id, em, await hashPassword(pw, iterations(c.env)), name, b.is_admin ? 1 : 0, now()).run();
  await audit(c, admin.id, "admin.user_create", em);
  return c.json(out({ id, email: em, display_name: name, is_admin: b.is_admin ? 1 : 0 }), 201);
});
