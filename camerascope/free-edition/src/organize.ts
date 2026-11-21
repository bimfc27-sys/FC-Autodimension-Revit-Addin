import { Hono } from "hono";
import { type CameraRow, cameraOut, canView } from "./cameras";
import { type AppEnv, type Env, type User, audit, body, email, fail, now, rateLimit, requireUser, str, uuid } from "./core";
import { publicOut, type PublicRow } from "./public";

export const organize = new Hono<AppEnv>();
const iso = (t: number) => new Date(t * 1000).toISOString();

async function cameraDict(env: Env, user: User, kind: string, id: string, favs?: Set<string>) {
  if (kind === "public") {
    const r = await env.DB.prepare("SELECT * FROM public_cameras WHERE id = ?").bind(id).first<PublicRow>();
    return r ? publicOut(r, favs ?? new Set([id])) : null;
  }
  if (!(await canView(env, user, id))) return null;
  const r = await env.DB.prepare("SELECT * FROM cameras WHERE id = ?").bind(id).first<CameraRow>();
  return r ? cameraOut(env, r, user) : null;
}

async function ensureVisible(env: Env, user: User, kind: string, id: string) {
  if (kind === "public") {
    if (!(await env.DB.prepare("SELECT 1 FROM public_cameras WHERE id = ?").bind(id).first())) fail(404, "Camera not found");
  } else if (kind === "private") {
    if (!(await canView(env, user, id))) fail(404, "Camera not found");
  } else fail(422, "camera_kind must be public or private");
}

// ------------------------------------------------------------------ favorites

organize.get("/favorites", async (c) => {
  const user = requireUser(c);
  const r = await c.env.DB.prepare("SELECT camera_kind, camera_id FROM favorites WHERE user_id = ? ORDER BY created_at DESC").bind(user.id).all<{ camera_kind: string; camera_id: string }>();
  const out = [];
  for (const f of r.results ?? []) {
    const d = await cameraDict(c.env, user, f.camera_kind, f.camera_id);
    if (d) out.push(d);
  }
  return c.json(out);
});

organize.put("/favorites/:kind/:id", async (c) => {
  const user = requireUser(c);
  const { kind, id } = c.req.param();
  await ensureVisible(c.env, user, kind, id);
  await c.env.DB.prepare("INSERT OR IGNORE INTO favorites (user_id, camera_kind, camera_id, created_at) VALUES (?, ?, ?, ?)").bind(user.id, kind, id, now()).run();
  return c.body(null, 204);
});

organize.delete("/favorites/:kind/:id", async (c) => {
  const user = requireUser(c);
  const { kind, id } = c.req.param();
  await c.env.DB.prepare("DELETE FROM favorites WHERE user_id = ? AND camera_kind = ? AND camera_id = ?").bind(user.id, kind, id).run();
  return c.body(null, 204);
});

// ------------------------------------------------------------------ groups

async function groupOut(env: Env, user: User, g: { id: string; name: string; position: number }) {
  const items = await env.DB.prepare("SELECT camera_kind, camera_id FROM camera_group_items WHERE group_id = ? ORDER BY position").bind(g.id).all<{ camera_kind: string; camera_id: string }>();
  const cams = [];
  for (const it of items.results ?? []) {
    const d = await cameraDict(env, user, it.camera_kind, it.camera_id, new Set());
    if (d) cams.push(d);
  }
  return { id: g.id, name: g.name, position: g.position, cameras: cams };
}

async function ownedGroup(env: Env, user: User, id: string) {
  const g = await env.DB.prepare("SELECT id, name, position FROM camera_groups WHERE id = ? AND owner_id = ?").bind(id, user.id).first<{ id: string; name: string; position: number }>();
  if (!g) fail(404, "Group not found");
  return g;
}

organize.get("/groups", async (c) => {
  const user = requireUser(c);
  const r = await c.env.DB.prepare("SELECT id, name, position FROM camera_groups WHERE owner_id = ? ORDER BY position, name").bind(user.id).all<{ id: string; name: string; position: number }>();
  return c.json(await Promise.all((r.results ?? []).map((g) => groupOut(c.env, user, g))));
});

organize.post("/groups", async (c) => {
  const user = requireUser(c);
  const b = await body(c);
  const n = (await c.env.DB.prepare("SELECT COUNT(*) AS n FROM camera_groups WHERE owner_id = ?").bind(user.id).first<{ n: number }>())?.n ?? 0;
  if (n >= 100) fail(409, "Too many groups");
  const g = { id: uuid(), name: str(b.name, "name", { min: 1, max: 120 }), position: n };
  await c.env.DB.prepare("INSERT INTO camera_groups (id, owner_id, name, position, created_at) VALUES (?, ?, ?, ?, ?)").bind(g.id, user.id, g.name, g.position, now()).run();
  return c.json(await groupOut(c.env, user, g), 201);
});

organize.patch("/groups/:id", async (c) => {
  const user = requireUser(c);
  const g = await ownedGroup(c.env, user, c.req.param("id"));
  const name = str((await body(c)).name, "name", { min: 1, max: 120 });
  await c.env.DB.prepare("UPDATE camera_groups SET name = ? WHERE id = ?").bind(name, g.id).run();
  return c.json(await groupOut(c.env, user, { ...g, name }));
});

organize.put("/groups/:id/items", async (c) => {
  const user = requireUser(c);
  const g = await ownedGroup(c.env, user, c.req.param("id"));
  const b = await body<{ items?: { camera_kind: string; camera_id: string }[] }>(c);
  const items = (b.items ?? []).slice(0, 64);
  for (const it of items) await ensureVisible(c.env, user, it.camera_kind, String(it.camera_id));
  const stmts = [c.env.DB.prepare("DELETE FROM camera_group_items WHERE group_id = ?").bind(g.id)];
  items.forEach((it, i) => stmts.push(
    c.env.DB.prepare("INSERT OR IGNORE INTO camera_group_items (group_id, camera_kind, camera_id, position) VALUES (?, ?, ?, ?)").bind(g.id, it.camera_kind, String(it.camera_id), i),
  ));
  await c.env.DB.batch(stmts);
  return c.json(await groupOut(c.env, user, g));
});

organize.delete("/groups/:id", async (c) => {
  const user = requireUser(c);
  const g = await ownedGroup(c.env, user, c.req.param("id"));
  await c.env.DB.prepare("DELETE FROM camera_groups WHERE id = ?").bind(g.id).run();
  return c.body(null, 204);
});

// ------------------------------------------------------------------ sites (buildings)

async function membership(env: Env, user: User, siteId: string, manager = false) {
  const m = await env.DB.prepare("SELECT role FROM site_members WHERE site_id = ? AND user_id = ?").bind(siteId, user.id).first<{ role: string }>();
  if (!m) fail(404, "Building not found");
  if (manager && m.role !== "manager") fail(403, "Building manager role required");
  return m;
}

async function siteOut(env: Env, s: { id: string; name: string; address: string | null }, role: string) {
  const cams = (await env.DB.prepare("SELECT COUNT(*) AS n FROM cameras WHERE site_id = ?").bind(s.id).first<{ n: number }>())?.n ?? 0;
  const members = (await env.DB.prepare("SELECT COUNT(*) AS n FROM site_members WHERE site_id = ?").bind(s.id).first<{ n: number }>())?.n ?? 0;
  return { id: s.id, name: s.name, address: s.address, role, camera_count: cams, member_count: members };
}

organize.get("/sites", async (c) => {
  const user = requireUser(c);
  const r = await c.env.DB.prepare("SELECT s.id, s.name, s.address, m.role FROM sites s JOIN site_members m ON m.site_id = s.id WHERE m.user_id = ? ORDER BY s.name")
    .bind(user.id).all<{ id: string; name: string; address: string | null; role: string }>();
  return c.json(await Promise.all((r.results ?? []).map((s) => siteOut(c.env, s, s.role))));
});

organize.post("/sites", async (c) => {
  const user = requireUser(c);
  await rateLimit(c.env, `site-create:${user.id}`, 20, 3600);
  const b = await body(c);
  const s = { id: uuid(), name: str(b.name, "name", { min: 1, max: 200 }), address: str(b.address, "address", { max: 300, required: false }) || null };
  await c.env.DB.batch([
    c.env.DB.prepare("INSERT INTO sites (id, name, address, created_by, created_at) VALUES (?, ?, ?, ?, ?)").bind(s.id, s.name, s.address, user.id, now()),
    c.env.DB.prepare("INSERT INTO site_members (site_id, user_id, role, created_at) VALUES (?, ?, 'manager', ?)").bind(s.id, user.id, now()),
  ]);
  await audit(c, user.id, "site.create", s.id);
  return c.json(await siteOut(c.env, s, "manager"), 201);
});

organize.delete("/sites/:id", async (c) => {
  const user = requireUser(c);
  const id = c.req.param("id");
  await membership(c.env, user, id, true);
  await c.env.DB.batch([
    c.env.DB.prepare("UPDATE cameras SET site_id = NULL WHERE site_id = ?").bind(id),
    c.env.DB.prepare("UPDATE devices SET site_id = NULL WHERE site_id = ?").bind(id),
    c.env.DB.prepare("DELETE FROM sites WHERE id = ?").bind(id),
  ]);
  await audit(c, user.id, "site.delete", id);
  return c.body(null, 204);
});

organize.get("/sites/:id/members", async (c) => {
  const user = requireUser(c);
  const id = c.req.param("id");
  await membership(c.env, user, id);
  const r = await c.env.DB.prepare("SELECT u.id, u.email, u.display_name, m.role, m.created_at FROM site_members m JOIN users u ON u.id = m.user_id WHERE m.site_id = ? ORDER BY u.display_name")
    .bind(id).all<{ id: string; email: string; display_name: string; role: string; created_at: number }>();
  return c.json((r.results ?? []).map((m) => ({ user_id: m.id, email: m.email, display_name: m.display_name, role: m.role, added_at: iso(m.created_at) })));
});

organize.post("/sites/:id/members", async (c) => {
  const user = requireUser(c);
  const id = c.req.param("id");
  await membership(c.env, user, id, true);
  await rateLimit(c.env, `site-invite:${user.id}`, 30, 3600);
  const b = await body(c);
  const role = b.role === "manager" ? "manager" : "viewer";
  const target = await c.env.DB.prepare("SELECT id, email, display_name FROM users WHERE email = ?").bind(email(b.email)).first<{ id: string; email: string; display_name: string }>();
  if (!target) fail(404, "No CameraScope account uses that e-mail. An administrator can create one under Manage → Settings.");
  await c.env.DB.prepare("INSERT INTO site_members (site_id, user_id, role, created_at) VALUES (?, ?, ?, ?) ON CONFLICT(site_id, user_id) DO UPDATE SET role = excluded.role")
    .bind(id, target.id, role, now()).run();
  await audit(c, user.id, "site.member_set", id, { member: target.id, role });
  return c.json({ user_id: target.id, email: target.email, display_name: target.display_name, role, added_at: iso(now()) }, 201);
});

organize.delete("/sites/:id/members/:uid", async (c) => {
  const user = requireUser(c);
  const { id, uid } = c.req.param();
  if (uid !== user.id) await membership(c.env, user, id, true);
  const m = await c.env.DB.prepare("SELECT role FROM site_members WHERE site_id = ? AND user_id = ?").bind(id, uid).first<{ role: string }>();
  if (!m) fail(404, "Member not found");
  if (m.role === "manager") {
    const n = (await c.env.DB.prepare("SELECT COUNT(*) AS n FROM site_members WHERE site_id = ? AND role = 'manager'").bind(id).first<{ n: number }>())?.n ?? 0;
    if (n <= 1) fail(409, "A building needs at least one manager");
  }
  await c.env.DB.batch([
    c.env.DB.prepare("UPDATE cameras SET site_id = NULL WHERE site_id = ? AND owner_id = ?").bind(id, uid),
    c.env.DB.prepare("DELETE FROM site_members WHERE site_id = ? AND user_id = ?").bind(id, uid),
  ]);
  await audit(c, user.id, "site.member_remove", id, { member: uid });
  return c.body(null, 204);
});

// ------------------------------------------------------------------ history

organize.post("/history", async (c) => {
  const user = requireUser(c);
  const b = await body(c);
  await ensureVisible(c.env, user, String(b.camera_kind), String(b.camera_id));
  await c.env.DB.batch([
    c.env.DB.prepare("INSERT INTO view_history (user_id, camera_kind, camera_id, viewed_at) VALUES (?, ?, ?, ?)").bind(user.id, b.camera_kind, b.camera_id, now()),
    c.env.DB.prepare("DELETE FROM view_history WHERE user_id = ? AND viewed_at < ?").bind(user.id, now() - 90 * 86400),
  ]);
  return c.body(null, 204);
});

organize.get("/history", async (c) => {
  const user = requireUser(c);
  const r = await c.env.DB.prepare("SELECT camera_kind, camera_id, MAX(viewed_at) AS at FROM view_history WHERE user_id = ? GROUP BY camera_kind, camera_id ORDER BY at DESC LIMIT 20")
    .bind(user.id).all<{ camera_kind: string; camera_id: string; at: number }>();
  const out = [];
  for (const h of r.results ?? []) {
    const d = await cameraDict(c.env, user, h.camera_kind, h.camera_id, new Set());
    if (d) out.push({ viewed_at: iso(h.at), camera: d });
  }
  return c.json(out);
});

organize.delete("/history", async (c) => {
  const user = requireUser(c);
  await c.env.DB.prepare("DELETE FROM view_history WHERE user_id = ?").bind(user.id).run();
  return c.body(null, 204);
});
