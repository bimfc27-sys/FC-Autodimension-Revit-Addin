import { Hono } from "hono";
import { BRANDS, brand, snapshotPathFor } from "./brands";
import {
  type AppEnv, type Ctx, type Env, type User, allowPrivate, audit, body, fail, host as hostV, int, memLimit, now, path as pathV,
  rateLimit, requireUser, str, uuid,
} from "./core";
import { open, seal } from "./crypto";
import { TargetRejected, cameraGet, isImage, tcpCheck, unreachableReason } from "./net";

export const MAX_AUTH_FAILURES = 3;
const MAX_IMAGE = 3 * 1024 * 1024;

export interface CameraRow {
  id: string; owner_id: string; device_id: string | null; site_id: string | null;
  name: string; brand: string; model: string | null; serial: string | null; notes: string | null;
  host: string; http_port: number; use_tls: number; rtsp_port: number | null; channel: number;
  snapshot_path: string | null; refresh_seconds: number;
  creds_ct: string | null; creds_iv: string | null; creds_key_id: string | null;
  authorization_basis: string; attested_at: number; latitude: number | null; longitude: number | null;
  status: string; status_reason: string | null; status_checked_at: number | null;
  video_mode: string; video_reason: string | null; video_verified_at: number | null;
  auth_failures: number; created_at: number; updated_at: number;
}

interface Creds { username: string; password: string }
const iso = (t: number | null) => (t ? new Date(t * 1000).toISOString() : null);

// ------------------------------------------------------------------ access

const VIEWABLE = `(c.owner_id = ?1 OR (c.site_id IS NOT NULL AND c.site_id IN (SELECT site_id FROM site_members WHERE user_id = ?1)))`;

export async function viewable(env: Env, user: User, id: string): Promise<CameraRow> {
  const r = await env.DB.prepare(`SELECT c.* FROM cameras c WHERE c.id = ?2 AND ${VIEWABLE}`).bind(user.id, id).first<CameraRow>();
  if (!r) fail(404, "Camera not found"); // 404 for others' cameras: existence is not revealed
  return r;
}

export async function owned(env: Env, user: User, id: string): Promise<CameraRow> {
  const r = await env.DB.prepare("SELECT * FROM cameras WHERE id = ? AND owner_id = ?").bind(id, user.id).first<CameraRow>();
  if (!r) fail(404, "Camera not found");
  return r;
}

export async function canView(env: Env, user: User, id: string): Promise<boolean> {
  return !!(await env.DB.prepare(`SELECT 1 FROM cameras c WHERE c.id = ?2 AND ${VIEWABLE}`).bind(user.id, id).first());
}

async function credentials(env: Env, cam: CameraRow): Promise<Creds> {
  let row: { id: string; owner_id: string; creds_ct: string | null; creds_iv: string | null; creds_key_id: string | null } = cam;
  if (!cam.creds_ct && cam.device_id) {
    const d = await env.DB.prepare("SELECT id, owner_id, creds_ct, creds_iv, creds_key_id FROM devices WHERE id = ?").bind(cam.device_id).first<typeof row>();
    if (d) row = d;
  }
  if (!row.creds_ct || !row.creds_iv || !row.creds_key_id) return { username: "", password: "" };
  return open<Creds>(env.CREDENTIAL_KEY, { ct: row.creds_ct, iv: row.creds_iv, kid: row.creds_key_id }, row.id, row.owner_id);
}

// ------------------------------------------------------------------ output

export async function cameraOut(env: Env, cam: CameraRow, user: User) {
  const isOwner = cam.owner_id === user.id;
  let username: string | null = null;
  if (isOwner && cam.creds_ct) {
    try { username = (await credentials(env, cam)).username || null; } catch { username = null; }
  }
  const dev = cam.device_id ? await env.DB.prepare("SELECT id, name, creds_ct FROM devices WHERE id = ?").bind(cam.device_id).first<{ id: string; name: string; creds_ct: string | null }>() : null;
  const site = cam.site_id ? await env.DB.prepare("SELECT id, name FROM sites WHERE id = ?").bind(cam.site_id).first<{ id: string; name: string }>() : null;
  const b = brand(cam.brand);
  const locked = cam.auth_failures >= MAX_AUTH_FAILURES;
  const available = cam.video_mode === "snapshot" && !locked && cam.status !== "auth_failed";
  return {
    kind: "private" as const,
    id: cam.id,
    name: cam.name,
    description: cam.notes,
    protocol: "snapshot",
    vendor: cam.brand,
    brand_label: b.label,
    model: cam.model,
    serial: cam.serial,
    is_owner: isOwner,
    access: isOwner ? "owner" : "shared",
    host: isOwner ? cam.host : null,
    port: isOwner ? cam.http_port : null,
    use_tls: !!cam.use_tls,
    config: isOwner
      ? { channel: cam.channel, snapshot_path: cam.snapshot_path, refresh_seconds: cam.refresh_seconds, rtsp_port: cam.rtsp_port, snapshot_allowed: true }
      : { snapshot_allowed: true },
    latitude: cam.latitude,
    longitude: cam.longitude,
    has_credentials: !!(cam.creds_ct || dev?.creds_ct),
    username,
    authorization_basis: cam.authorization_basis,
    authorization_attested_at: iso(cam.attested_at),
    status: locked ? "auth_failed" : cam.status,
    connection: "cloud",
    gateway: null,
    device: dev ? { id: dev.id, name: dev.name } : null,
    site,
    channel: cam.device_id ? cam.channel : null,
    reachable_checked_at: iso(cam.status_checked_at),
    last_error: isOwner ? cam.status_reason : null,
    last_checked_at: iso(cam.status_checked_at),
    created_at: iso(cam.created_at),
    video: {
      available,
      mode: available ? "snapshot" : "none",
      refresh_seconds: cam.refresh_seconds,
      verified_at: iso(cam.video_verified_at),
      reason: available ? null : locked ? "The camera rejected the stored credentials 3 times. Update them to unlock." : cam.video_reason ?? "Not verified yet. Run diagnostics.",
    },
  };
}

// ------------------------------------------------------------------ verification

export interface Step { name: string; ok: boolean; detail: string; duration_ms: number }

async function step(steps: Step[], name: string, fn: () => Promise<string>): Promise<boolean> {
  const t = Date.now();
  try {
    steps.push({ name, ok: true, detail: await fn(), duration_ms: Date.now() - t });
    return true;
  } catch (e) {
    steps.push({ name, ok: false, detail: (e as Error).message.slice(0, 300), duration_ms: Date.now() - t });
    return false;
  }
}

/**
 * Runs the checks and records the outcome. Video is enabled ONLY when a real
 * image came back from the camera's documented snapshot endpoint.
 * Makes at most ONE authentication attempt.
 */
export async function verifyCamera(env: Env, cam: CameraRow): Promise<{ ok: boolean; steps: Step[] }> {
  const steps: Step[] = [];
  const b = brand(cam.brand);
  const t = now();
  steps.push({ name: "Configuration", ok: true, detail: `${b.label}, ${cam.use_tls ? "HTTPS" : "HTTP"} port ${cam.http_port}${cam.device_id ? `, channel ${cam.channel}` : ""}`, duration_ms: 0 });

  const lanReason = allowPrivate(env) ? null : unreachableReason(cam.host);
  if (lanReason) {
    steps.push({ name: "Internet address", ok: false, detail: lanReason, duration_ms: 0 });
    await env.DB.prepare("UPDATE cameras SET status='not_internet_reachable', status_reason=?, status_checked_at=?, video_mode='none', video_reason=? WHERE id=?")
      .bind(lanReason, t, lanReason, cam.id).run();
    return { ok: false, steps };
  }
  steps.push({ name: "Internet address", ok: true, detail: `${cam.host} is a public address or name`, duration_ms: 0 });

  const reachable = await step(steps, "Reachable from Cloudflare", async () => {
    await tcpCheck(cam.host, cam.http_port, !!cam.use_tls, allowPrivate(env));
    return `TCP ${cam.host}:${cam.http_port} answered`;
  });
  if (!reachable) {
    const why = `Not reachable from the internet on port ${cam.http_port}. Check the router port-forward, the camera is powered, and the public IP/DDNS name is current.`;
    steps.push({ name: "Video", ok: false, detail: why, duration_ms: 0 });
    await env.DB.prepare("UPDATE cameras SET status='offline', status_reason=?, status_checked_at=?, video_mode='none', video_reason=? WHERE id=?")
      .bind(steps.at(-2)!.detail, t, why, cam.id).run();
    return { ok: false, steps };
  }

  const sp = snapshotPathFor(b, cam.channel, cam.snapshot_path);
  if (!sp.path) {
    steps.push({ name: "Video", ok: false, detail: sp.reason!, duration_ms: 0 });
    await env.DB.prepare("UPDATE cameras SET status='online', status_reason=NULL, status_checked_at=?, video_mode='none', video_reason=? WHERE id=?")
      .bind(t, sp.reason, cam.id).run();
    return { ok: false, steps };
  }

  const creds = await credentials(env, cam);
  let authFailed = false;
  const gotImage = await step(steps, "Login + snapshot", async () => {
    const pathWithAuth = b.queryAuth && creds.username ? sp.path + b.queryAuth(creds.username, creds.password) : sp.path!;
    const r = await cameraGet({
      host: cam.host, port: cam.http_port, tls: !!cam.use_tls, path: pathWithAuth,
      username: b.queryAuth ? undefined : creds.username, password: creds.password,
      timeoutMs: 10000, maxBytes: MAX_IMAGE, allowPrivate: allowPrivate(env),
    });
    if (r.status === 401 || r.status === 403 || (b.queryAuth && r.status === 200 && !isImage(r) && /error|login|auth/i.test(new TextDecoder().decode(r.body.slice(0, 400))))) {
      authFailed = true;
      throw new Error(`Camera rejected the credentials (HTTP ${r.status}). CameraScope tried once and will not retry.`);
    }
    if (r.status !== 200) throw new Error(`Snapshot endpoint returned HTTP ${r.status}. Check the brand/channel or snapshot path.`);
    const ct = isImage(r);
    if (!ct) throw new Error(`Snapshot endpoint did not return an image (content-type: ${r.headers.get("content-type") ?? "none"}).`);
    return `${ct}, ${Math.round(r.body.length / 1024)} kB received`;
  });

  if (gotImage) {
    steps.push({ name: "Video", ok: true, detail: `Snapshot view enabled (refreshes every ${cam.refresh_seconds} s). Live RTSP video is not available in the Free Edition.`, duration_ms: 0 });
    await env.DB.prepare("UPDATE cameras SET status='online', status_reason=NULL, status_checked_at=?, video_mode='snapshot', video_reason=NULL, video_verified_at=?, auth_failures=0 WHERE id=?")
      .bind(t, t, cam.id).run();
    return { ok: true, steps };
  }
  const why = steps.at(-1)!.detail;
  if (authFailed) {
    await env.DB.prepare("UPDATE cameras SET status='auth_failed', status_reason=?, status_checked_at=?, video_mode='none', video_reason=?, auth_failures=auth_failures+1 WHERE id=?")
      .bind(why, t, why, cam.id).run();
  } else {
    await env.DB.prepare("UPDATE cameras SET status='online', status_reason=?, status_checked_at=?, video_mode='none', video_reason=? WHERE id=?")
      .bind(why, t, why, cam.id).run();
  }
  steps.push({ name: "Video", ok: false, detail: "Video stays disabled until a snapshot is verified.", duration_ms: 0 });
  return { ok: false, steps };
}

/** Credential-free reachability refresh for stale cameras (runs after the response). */
export async function refreshStale(env: Env, cams: CameraRow[]): Promise<void> {
  const stale = cams.filter((c) => !c.status_checked_at || now() - c.status_checked_at > 300).slice(0, 10);
  await Promise.all(stale.map(async (c) => {
    if (!allowPrivate(env) && unreachableReason(c.host)) return;
    let ok = false;
    try { await tcpCheck(c.host, c.http_port, !!c.use_tls, allowPrivate(env), 5000); ok = true; } catch { ok = false; }
    const status = c.status === "auth_failed" ? "auth_failed" : ok ? "online" : "offline";
    await env.DB.prepare("UPDATE cameras SET status=?, status_checked_at=?, status_reason=CASE WHEN ?=1 THEN status_reason ELSE ? END WHERE id=?")
      .bind(status, now(), ok ? 1 : 0, `Not reachable from the internet on port ${c.http_port}`, c.id).run();
  }));
}

// ------------------------------------------------------------------ routes

export const cameras = new Hono<AppEnv>();

cameras.get("/brands", (c) => c.json(Object.values(BRANDS).map((b) => ({
  id: b.id, label: b.label, kinds: b.kinds, default_http_port: b.defaultHttpPort, video: b.video,
  has_default_snapshot: !!b.snapshotPath, channel_discovery: b.channelDiscovery, notes: b.notes, docs: b.docs,
}))));

cameras.get("/", async (c) => {
  const user = requireUser(c);
  const scope = c.req.query("scope") ?? "all";
  let where = VIEWABLE;
  if (scope === "mine") where = "c.owner_id = ?1";
  if (scope === "shared") where = `c.owner_id != ?1 AND ${VIEWABLE}`;
  const rows = ((await c.env.DB.prepare(`SELECT c.* FROM cameras c WHERE ${where} ORDER BY c.name`).bind(user.id).all<CameraRow>()).results ?? []);
  c.executionCtx.waitUntil(refreshStale(c.env, rows.filter((r) => r.owner_id === user.id)));
  return c.json(await Promise.all(rows.map((r) => cameraOut(c.env, r, user))));
});

async function checkSite(c: Ctx, user: User, siteId: unknown): Promise<string | null> {
  if (!siteId) return null;
  const m = await c.env.DB.prepare("SELECT role FROM site_members WHERE site_id = ? AND user_id = ?").bind(String(siteId), user.id).first<{ role: string }>();
  if (!m || m.role !== "manager") fail(403, "Only building managers can add cameras to a building");
  return String(siteId);
}

function coord(v: unknown, lo: number, hi: number): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  if (!Number.isFinite(n) || n < lo || n > hi) fail(422, "invalid coordinate");
  return n;
}

cameras.post("/", async (c) => {
  const user = requireUser(c);
  await rateLimit(c.env, `cam-create:${user.id}`, 60, 3600);
  const b = await body(c);
  if (b.authorization_attested !== true) fail(422, "You must confirm you own or are authorised to access this camera");
  const basis = b.authorization_basis === "authorized_user" ? "authorized_user" : "owner";
  const br = brand(String(b.brand ?? "generic"));
  const id = uuid();
  const t = now();
  const cam: CameraRow = {
    id, owner_id: user.id, device_id: null, site_id: await checkSite(c, user, b.site_id),
    name: str(b.name, "name", { min: 1, max: 200 }), brand: br.id,
    model: str(b.model, "model", { max: 120, required: false }) || null,
    serial: str(b.serial, "serial", { max: 120, required: false }) || null,
    notes: str(b.notes, "notes", { max: 2000, required: false }) || null,
    host: hostV(b.host), http_port: int(b.port, "port", 1, 65535, br.defaultHttpPort), use_tls: b.use_tls ? 1 : 0,
    rtsp_port: b.rtsp_port ? int(b.rtsp_port, "rtsp_port", 1, 65535) : null, channel: int(b.channel, "channel", 1, 256, 1),
    snapshot_path: pathV(b.snapshot_path, "snapshot_path"), refresh_seconds: int(b.refresh_seconds, "refresh_seconds", 2, 60, 3),
    creds_ct: null, creds_iv: null, creds_key_id: null, authorization_basis: basis, attested_at: t,
    latitude: coord(b.latitude, -90, 90), longitude: coord(b.longitude, -180, 180),
    status: "unknown", status_reason: null, status_checked_at: null, video_mode: "none", video_reason: null, video_verified_at: null,
    auth_failures: 0, created_at: t, updated_at: t,
  };
  const cr = (b.credentials ?? {}) as Record<string, unknown>;
  const u = str(cr.username, "username", { max: 128, required: false }), p = typeof cr.password === "string" ? cr.password.slice(0, 256) : "";
  if (u || p) {
    const s = await seal(c.env.CREDENTIAL_KEY, { username: u, password: p }, id, user.id);
    cam.creds_ct = s.ct; cam.creds_iv = s.iv; cam.creds_key_id = s.kid;
  }
  await c.env.DB.prepare(
    `INSERT INTO cameras (id, owner_id, device_id, site_id, name, brand, model, serial, notes, host, http_port, use_tls, rtsp_port, channel,
      snapshot_path, refresh_seconds, creds_ct, creds_iv, creds_key_id, authorization_basis, attested_at, latitude, longitude,
      status, video_mode, auth_failures, created_at, updated_at)
     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
  ).bind(cam.id, cam.owner_id, null, cam.site_id, cam.name, cam.brand, cam.model, cam.serial, cam.notes, cam.host, cam.http_port, cam.use_tls,
    cam.rtsp_port, cam.channel, cam.snapshot_path, cam.refresh_seconds, cam.creds_ct, cam.creds_iv, cam.creds_key_id, basis, t,
    cam.latitude, cam.longitude, "unknown", "none", 0, t, t).run();
  await audit(c, user.id, "camera.create", id, { brand: cam.brand, basis });
  const v = await verifyCamera(c.env, cam);
  await saveDiagnostics(c.env, user.id, "private", id, v);
  const fresh = await owned(c.env, user, id);
  return c.json({ ...(await cameraOut(c.env, fresh, user)), verification: v }, 201);
});

cameras.get("/:id", async (c) => {
  const user = requireUser(c);
  return c.json(await cameraOut(c.env, await viewable(c.env, user, c.req.param("id")), user));
});

cameras.patch("/:id", async (c) => {
  const user = requireUser(c);
  const cam = await owned(c.env, user, c.req.param("id"));
  const b = await body(c);
  const sets: string[] = [];
  const vals: unknown[] = [];
  const set = (col: string, v: unknown) => { sets.push(`${col} = ?`); vals.push(v); };
  let connectionChanged = false;
  if ("name" in b) set("name", str(b.name, "name", { min: 1, max: 200 }));
  if ("notes" in b) set("notes", str(b.notes, "notes", { max: 2000, required: false }) || null);
  if ("model" in b) set("model", str(b.model, "model", { max: 120, required: false }) || null);
  if ("serial" in b) set("serial", str(b.serial, "serial", { max: 120, required: false }) || null);
  if ("refresh_seconds" in b) set("refresh_seconds", int(b.refresh_seconds, "refresh_seconds", 2, 60));
  if ("latitude" in b) set("latitude", coord(b.latitude, -90, 90));
  if ("longitude" in b) set("longitude", coord(b.longitude, -180, 180));
  if ("site_id" in b) set("site_id", await checkSite(c, user, b.site_id));
  if ("host" in b) { set("host", hostV(b.host)); connectionChanged = true; }
  if ("port" in b) { set("http_port", int(b.port, "port", 1, 65535)); connectionChanged = true; }
  if ("use_tls" in b) { set("use_tls", b.use_tls ? 1 : 0); connectionChanged = true; }
  if ("channel" in b) { set("channel", int(b.channel, "channel", 1, 256)); connectionChanged = true; }
  if ("snapshot_path" in b) { set("snapshot_path", pathV(b.snapshot_path, "snapshot_path")); connectionChanged = true; }
  if ("brand" in b) { set("brand", brand(String(b.brand)).id); connectionChanged = true; }
  if (b.credentials && typeof b.credentials === "object") {
    const cr = b.credentials as Record<string, unknown>;
    const s = await seal(c.env.CREDENTIAL_KEY, { username: str(cr.username, "username", { max: 128, required: false }), password: String(cr.password ?? "").slice(0, 256) }, cam.id, cam.owner_id);
    set("creds_ct", s.ct); set("creds_iv", s.iv); set("creds_key_id", s.kid); set("auth_failures", 0);
    connectionChanged = true;
  }
  if (connectionChanged) { set("video_mode", "none"); set("video_reason", "Connection settings changed: run diagnostics to verify again."); set("status", "unknown"); }
  set("updated_at", now());
  await c.env.DB.prepare(`UPDATE cameras SET ${sets.join(", ")} WHERE id = ?`).bind(...vals, cam.id).run();
  await audit(c, user.id, "camera.update", cam.id, { fields: Object.keys(b).filter((k) => k !== "credentials").concat(b.credentials ? ["credentials"] : []) });
  return c.json(await cameraOut(c.env, await owned(c.env, user, cam.id), user));
});

cameras.delete("/:id", async (c) => {
  const user = requireUser(c);
  const cam = await owned(c.env, user, c.req.param("id"));
  await c.env.DB.batch([
    c.env.DB.prepare("DELETE FROM cameras WHERE id = ?").bind(cam.id),
    c.env.DB.prepare("DELETE FROM favorites WHERE camera_kind='private' AND camera_id = ?").bind(cam.id),
    c.env.DB.prepare("DELETE FROM camera_group_items WHERE camera_kind='private' AND camera_id = ?").bind(cam.id),
  ]);
  await audit(c, user.id, "camera.delete", cam.id);
  return c.body(null, 204);
});

cameras.get("/:id/playback", async (c) => {
  const user = requireUser(c);
  const cam = await viewable(c.env, user, c.req.param("id"));
  const o = await cameraOut(c.env, cam, user);
  if (!o.video.available) fail(409, `No video: ${o.video.reason}`);
  await audit(c, user.id, "camera.view", cam.id, { shared: cam.owner_id !== user.id });
  return c.json({
    kind: "private", camera_id: cam.id, mode: "image",
    hls_url: null, whep_url: null, page_url: null,
    image_url: `/api/cameras/${cam.id}/snapshot-view`, refresh_seconds: cam.refresh_seconds,
    quality: "snapshot", qualities: [],
    snapshot_allowed: true, snapshot_url: `/api/cameras/${cam.id}/snapshot`,
    attribution: `Snapshot view · every ${cam.refresh_seconds} s`,
  });
});

async function fetchSnapshot(c: Ctx, cam: CameraRow): Promise<{ data: Uint8Array; ct: string }> {
  if (cam.video_mode !== "snapshot" || cam.auth_failures >= MAX_AUTH_FAILURES) fail(409, `No video: ${cam.video_reason ?? "not verified"}`);
  const b = brand(cam.brand);
  const sp = snapshotPathFor(b, cam.channel, cam.snapshot_path);
  if (!sp.path) fail(409, `No video: ${sp.reason}`);
  const creds = await credentials(c.env, cam);
  const p = b.queryAuth && creds.username ? sp.path + b.queryAuth(creds.username, creds.password) : sp.path;
  let r;
  try {
    r = await cameraGet({ host: cam.host, port: cam.http_port, tls: !!cam.use_tls, path: p, username: b.queryAuth ? undefined : creds.username,
      password: creds.password, timeoutMs: 10000, maxBytes: MAX_IMAGE, allowPrivate: allowPrivate(c.env) });
  } catch (e) {
    const why = e instanceof TargetRejected ? e.message : `Camera unreachable: ${(e as Error).message}`;
    if (cam.status !== "offline") await c.env.DB.prepare("UPDATE cameras SET status='offline', status_reason=?, status_checked_at=? WHERE id=?").bind(why, now(), cam.id).run();
    fail(504, why);
  }
  if (r.status === 401 || r.status === 403) {
    await c.env.DB.prepare("UPDATE cameras SET status='auth_failed', status_reason=?, auth_failures=auth_failures+1, video_mode='none', video_reason=? WHERE id=?")
      .bind("Credentials rejected", "The camera rejected the stored credentials. Update them and run diagnostics.", cam.id).run();
    fail(502, "Camera rejected the stored credentials");
  }
  const ct = r.status === 200 ? isImage(r) : null;
  if (!ct) fail(502, `Camera returned HTTP ${r.status} without an image`);
  if (cam.status !== "online") await c.env.DB.prepare("UPDATE cameras SET status='online', status_reason=NULL, status_checked_at=? WHERE id=?").bind(now(), cam.id).run();
  return { data: r.body, ct };
}

cameras.get("/:id/snapshot-view", async (c) => {
  const user = requireUser(c);
  memLimit(`snap:${user.id}`, 120, 60000);
  const cam = await viewable(c.env, user, c.req.param("id"));
  const { data, ct } = await fetchSnapshot(c, cam);
  return new Response(data, { headers: { "Content-Type": ct, "Cache-Control": "no-store, private" } });
});

cameras.get("/:id/snapshot", async (c) => {
  const user = requireUser(c);
  memLimit(`snapdl:${user.id}`, 30, 60000);
  const cam = await viewable(c.env, user, c.req.param("id"));
  const { data, ct } = await fetchSnapshot(c, cam);
  await audit(c, user.id, "camera.snapshot", cam.id);
  return new Response(data, { headers: { "Content-Type": ct, "Cache-Control": "no-store", "Content-Disposition": `attachment; filename="snapshot-${cam.id.slice(0, 8)}.jpg"` } });
});

// ------------------------------------------------------------------ diagnostics

export async function saveDiagnostics(env: Env, userId: string, kind: string, id: string, v: { ok: boolean; steps: Step[] }) {
  return env.DB.prepare("INSERT INTO diagnostic_runs (user_id, camera_kind, camera_id, ok, steps, created_at) VALUES (?, ?, ?, ?, ?, ?) RETURNING id, created_at")
    .bind(userId, kind, id, v.ok ? 1 : 0, JSON.stringify(v.steps), now()).first<{ id: number; created_at: number }>();
}

export const diagnostics = new Hono<AppEnv>();

diagnostics.post("/", async (c) => {
  const user = requireUser(c);
  await rateLimit(c.env, `diag:${user.id}`, 10, 60);
  const b = await body(c);
  if (b.camera_kind !== "private") fail(400, "Diagnostics are available for your own cameras");
  const cam = await owned(c.env, user, String(b.camera_id));
  if (cam.auth_failures >= MAX_AUTH_FAILURES) {
    fail(423, "This camera rejected the stored credentials several times. Update the credentials to unlock it - CameraScope never retries or guesses passwords.");
  }
  await rateLimit(c.env, `diag-cam:${cam.id}`, 4, 300);
  const v = await verifyCamera(c.env, cam);
  const row = await saveDiagnostics(c.env, user.id, "private", cam.id, v);
  await audit(c, user.id, "camera.diagnostics", cam.id, { ok: v.ok });
  return c.json({ id: row?.id ?? null, camera_kind: "private", camera_id: cam.id, ok: v.ok, steps: v.steps, created_at: iso(row?.created_at ?? now()) });
});

diagnostics.get("/", async (c) => {
  const user = requireUser(c);
  const r = await c.env.DB.prepare("SELECT * FROM diagnostic_runs WHERE user_id = ? ORDER BY created_at DESC LIMIT 25").bind(user.id)
    .all<{ id: number; camera_kind: string; camera_id: string; ok: number; steps: string; created_at: number }>();
  return c.json((r.results ?? []).map((x) => ({ id: x.id, camera_kind: x.camera_kind, camera_id: x.camera_id, ok: !!x.ok, steps: JSON.parse(x.steps), created_at: iso(x.created_at) })));
});
