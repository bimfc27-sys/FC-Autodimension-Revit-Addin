import { Hono } from "hono";
import { brand } from "./brands";
import { type CameraRow, cameraOut, verifyCamera, saveDiagnostics } from "./cameras";
import { type AppEnv, type Env, type User, allowPrivate, audit, body, credentialKey, fail, host as hostV, int, now, rateLimit, requireUser, str, uuid } from "./core";
import { open, seal } from "./crypto";
import { cameraGet } from "./net";

interface DeviceRow {
  id: string; owner_id: string; site_id: string | null; name: string; brand: string; model: string | null; serial: string | null;
  host: string; http_port: number; use_tls: number; creds_ct: string | null; creds_iv: string | null; creds_key_id: string | null;
  authorization_basis: string; attested_at: number; status: string; status_reason: string | null; status_checked_at: number | null;
  auth_failures: number; created_at: number;
}

async function ownedDevice(env: Env, user: User, id: string): Promise<DeviceRow> {
  const d = await env.DB.prepare("SELECT * FROM devices WHERE id = ? AND owner_id = ?").bind(id, user.id).first<DeviceRow>();
  if (!d) fail(404, "Recorder not found");
  return d;
}

async function deviceOut(env: Env, d: DeviceRow) {
  const n = (await env.DB.prepare("SELECT COUNT(*) AS n FROM cameras WHERE device_id = ?").bind(d.id).first<{ n: number }>())?.n ?? 0;
  const site = d.site_id ? await env.DB.prepare("SELECT id, name FROM sites WHERE id = ?").bind(d.site_id).first() : null;
  const b = brand(d.brand);
  return {
    id: d.id, name: d.name, kind: "nvr", protocol: "vendor_api", vendor: d.brand, brand_label: b.label, host: d.host, port: d.http_port,
    rtsp_port: null, use_tls: !!d.use_tls, model: d.model, serial: d.serial, status: d.status, last_error: d.status_reason,
    last_discovery_at: d.status_checked_at ? new Date(d.status_checked_at * 1000).toISOString() : null,
    connection: "cloud", gateway: null, site, channel_count: n, channel_discovery: b.channelDiscovery,
  };
}

export const devices = new Hono<AppEnv>();

devices.get("/", async (c) => {
  const user = requireUser(c);
  const r = await c.env.DB.prepare("SELECT * FROM devices WHERE owner_id = ? ORDER BY name").bind(user.id).all<DeviceRow>();
  return c.json(await Promise.all((r.results ?? []).map((d) => deviceOut(c.env, d))));
});

devices.post("/", async (c) => {
  const user = requireUser(c);
  await rateLimit(c.env, `dev-create:${user.id}`, 30, 3600);
  const b = await body(c);
  if (b.authorization_attested !== true) fail(422, "You must confirm you own or are authorised to access this recorder");
  const br = brand(String(b.brand ?? "generic"));
  if (!br.kinds.includes("nvr")) fail(422, `${br.label} recorders are not supported in the Free Edition`);
  let siteId: string | null = null;
  if (b.site_id) {
    const m = await c.env.DB.prepare("SELECT role FROM site_members WHERE site_id = ? AND user_id = ?").bind(String(b.site_id), user.id).first<{ role: string }>();
    if (!m || m.role !== "manager") fail(403, "Only building managers can add recorders to a building");
    siteId = String(b.site_id);
  }
  const id = uuid(), t = now();
  const cr = (b.credentials ?? {}) as Record<string, unknown>;
  const s = await seal(await credentialKey(c.env), { username: str(cr.username, "username", { max: 128, required: false }), password: String(cr.password ?? "").slice(0, 256) }, id, user.id);
  const d: DeviceRow = {
    id, owner_id: user.id, site_id: siteId, name: str(b.name, "name", { min: 1, max: 200 }), brand: br.id,
    model: str(b.model, "model", { max: 120, required: false }) || null, serial: str(b.serial, "serial", { max: 120, required: false }) || null,
    host: hostV(b.host), http_port: int(b.port, "port", 1, 65535, br.defaultHttpPort), use_tls: b.use_tls ? 1 : 0,
    creds_ct: s.ct, creds_iv: s.iv, creds_key_id: s.kid, authorization_basis: b.authorization_basis === "authorized_user" ? "authorized_user" : "owner",
    attested_at: t, status: "unknown", status_reason: null, status_checked_at: null, auth_failures: 0, created_at: t,
  };
  await c.env.DB.prepare(
    `INSERT INTO devices (id, owner_id, site_id, name, brand, model, serial, host, http_port, use_tls, creds_ct, creds_iv, creds_key_id,
      authorization_basis, attested_at, status, auth_failures, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
  ).bind(d.id, d.owner_id, d.site_id, d.name, d.brand, d.model, d.serial, d.host, d.http_port, d.use_tls, d.creds_ct, d.creds_iv, d.creds_key_id,
    d.authorization_basis, t, "unknown", 0, t).run();
  await audit(c, user.id, "device.create", id, { brand: d.brand });
  return c.json(await deviceOut(c.env, d), 201);
});

devices.patch("/:id", async (c) => {
  const user = requireUser(c);
  const d = await ownedDevice(c.env, user, c.req.param("id"));
  const b = await body(c);
  if (b.name) await c.env.DB.prepare("UPDATE devices SET name = ? WHERE id = ?").bind(str(b.name, "name", { min: 1, max: 200 }), d.id).run();
  if (b.credentials && typeof b.credentials === "object") {
    const cr = b.credentials as Record<string, unknown>;
    const s = await seal(await credentialKey(c.env), { username: str(cr.username, "username", { max: 128, required: false }), password: String(cr.password ?? "").slice(0, 256) }, d.id, d.owner_id);
    await c.env.DB.batch([
      c.env.DB.prepare("UPDATE devices SET creds_ct=?, creds_iv=?, creds_key_id=?, auth_failures=0, status='unknown' WHERE id=?").bind(s.ct, s.iv, s.kid, d.id),
      c.env.DB.prepare("UPDATE cameras SET auth_failures=0, status='unknown', video_mode='none', video_reason='Recorder credentials changed: run diagnostics to verify again.' WHERE device_id=?").bind(d.id),
    ]);
  }
  await audit(c, user.id, "device.update", d.id);
  return c.json(await deviceOut(c.env, await ownedDevice(c.env, user, d.id)));
});

devices.delete("/:id", async (c) => {
  const user = requireUser(c);
  const d = await ownedDevice(c.env, user, c.req.param("id"));
  await c.env.DB.prepare("DELETE FROM devices WHERE id = ?").bind(d.id).run(); // channel cameras cascade
  await audit(c, user.id, "device.delete", d.id);
  return c.body(null, 204);
});

/** Channel discovery through DOCUMENTED vendor HTTP APIs (Hikvision ISAPI, Dahua configManager). */
devices.post("/:id/discover", async (c) => {
  const user = requireUser(c);
  const d = await ownedDevice(c.env, user, c.req.param("id"));
  await rateLimit(c.env, `dev-discover:${d.id}`, 5, 300);
  const br = brand(d.brand);
  if (!br.channelDiscovery) fail(409, `${br.label}: automatic channel discovery is not available. Add channels manually.`);
  if (d.auth_failures >= 3) fail(423, "The recorder rejected the stored credentials several times. Update them to unlock.");
  const creds = await open<{ username: string; password: string }>(await credentialKey(c.env), { ct: d.creds_ct!, iv: d.creds_iv!, kid: d.creds_key_id! }, d.id, d.owner_id);
  const get = (path: string) => cameraGet({ host: d.host, port: d.http_port, tls: !!d.use_tls, path, username: creds.username, password: creds.password, timeoutMs: 10000, maxBytes: 512 * 1024, allowPrivate: allowPrivate(c.env) });
  let chans: { index: number; name: string }[] = [];
  try {
    if (br.channelDiscovery === "hikvision") {
      for (const p of ["/ISAPI/ContentMgmt/InputProxy/channels", "/ISAPI/System/Video/inputs/channels"]) {
        const r = await get(p);
        if (r.status === 401 || r.status === 403) throw new Error("AUTH");
        if (r.status !== 200) continue;
        const xml = new TextDecoder().decode(r.body);
        for (const m of xml.matchAll(/<(?:InputProxyChannel|VideoInputChannel)[^>]*>([\s\S]*?)<\/(?:InputProxyChannel|VideoInputChannel)>/g)) {
          const idx = parseInt(m[1].match(/<id>(\d+)<\/id>/)?.[1] ?? "0", 10);
          const name = m[1].match(/<name>([^<]*)<\/name>/)?.[1]?.trim();
          if (idx > 0) chans.push({ index: idx, name: name || `Channel ${idx}` });
        }
        if (chans.length) break;
        // only one auth attempt overall: stop after the first authenticated answer
        if (r.authAttempted) break;
      }
    } else {
      const r = await get("/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle");
      if (r.status === 401 || r.status === 403) throw new Error("AUTH");
      if (r.status !== 200) throw new Error(`Recorder returned HTTP ${r.status}`);
      for (const m of new TextDecoder().decode(r.body).matchAll(/table\.ChannelTitle\[(\d+)\]\.Name=(.*)/g)) {
        chans.push({ index: parseInt(m[1], 10) + 1, name: m[2].trim() || `Channel ${parseInt(m[1], 10) + 1}` });
      }
    }
  } catch (e) {
    const auth = (e as Error).message === "AUTH";
    const why = auth ? "Recorder rejected the credentials (one attempt, not retried)." : (e as Error).message;
    await c.env.DB.prepare(`UPDATE devices SET status=?, status_reason=?, status_checked_at=?${auth ? ", auth_failures=auth_failures+1" : ""} WHERE id=?`)
      .bind(auth ? "auth_failed" : "offline", why, now(), d.id).run();
    fail(502, why);
  }
  if (!chans.length) fail(502, "The recorder did not report any channels. Add channels manually.");
  chans = chans.slice(0, 128);
  await c.env.DB.prepare("UPDATE devices SET status='online', status_reason=NULL, status_checked_at=?, auth_failures=0 WHERE id=?").bind(now(), d.id).run();
  const existing = new Map(((await c.env.DB.prepare("SELECT id, channel FROM cameras WHERE device_id = ?").bind(d.id).all<{ id: string; channel: number }>()).results ?? []).map((x) => [x.channel, x.id]));
  await audit(c, user.id, "device.discover", d.id, { channels: chans.length });
  return c.json({ device: await deviceOut(c.env, await ownedDevice(c.env, user, d.id)), channels: chans.map((ch) => ({ ...ch, main_profile: null, sub_profile: null, resolution: null, has_sub_stream: false, imported_camera_id: existing.get(ch.index) ?? null })) });
});

/** Add channels (discovered or entered manually) as cameras. They inherit the recorder's credentials. */
devices.post("/:id/import", async (c) => {
  const user = requireUser(c);
  const d = await ownedDevice(c.env, user, c.req.param("id"));
  const b = await body<{ channels?: { index: unknown; name: unknown }[] }>(c);
  const list = Array.isArray(b.channels) ? b.channels.slice(0, 64) : [];
  if (!list.length) fail(422, "channels: select at least one channel");
  const existing = new Set(((await c.env.DB.prepare("SELECT channel FROM cameras WHERE device_id = ?").bind(d.id).all<{ channel: number }>()).results ?? []).map((x) => x.channel));
  const br = brand(d.brand);
  const created: CameraRow[] = [];
  const t = now();
  for (const ch of list) {
    const idx = int(ch.index, "channel", 1, 256);
    if (existing.has(idx)) continue;
    existing.add(idx);
    const cam: CameraRow = {
      id: uuid(), owner_id: user.id, device_id: d.id, site_id: d.site_id, name: str(ch.name, "name", { min: 1, max: 200 }), brand: br.id,
      model: d.model, serial: null, notes: null, host: d.host, http_port: d.http_port, use_tls: d.use_tls, rtsp_port: null, channel: idx,
      snapshot_path: null, refresh_seconds: 3, creds_ct: null, creds_iv: null, creds_key_id: null,
      authorization_basis: d.authorization_basis, attested_at: d.attested_at, latitude: null, longitude: null,
      status: "unknown", status_reason: null, status_checked_at: null, video_mode: "none", video_reason: "Not verified yet. Run diagnostics.", video_verified_at: null,
      auth_failures: 0, created_at: t, updated_at: t,
    };
    await c.env.DB.prepare(
      `INSERT INTO cameras (id, owner_id, device_id, site_id, name, brand, model, host, http_port, use_tls, channel, refresh_seconds,
        authorization_basis, attested_at, status, video_mode, video_reason, auth_failures, created_at, updated_at)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
    ).bind(cam.id, cam.owner_id, d.id, cam.site_id, cam.name, cam.brand, cam.model, cam.host, cam.http_port, cam.use_tls, idx, 3,
      cam.authorization_basis, cam.attested_at, "unknown", "none", cam.video_reason, 0, t, t).run();
    created.push(cam);
  }
  // Verify the first channels right away (each = 1 TCP check + 1 snapshot request with one auth attempt).
  for (const cam of created.slice(0, 6)) {
    const v = await verifyCamera(c.env, cam);
    await saveDiagnostics(c.env, user.id, "private", cam.id, v);
    if (v.steps.some((s) => /rejected the credentials/.test(s.detail))) break; // never repeat a failing login
  }
  await audit(c, user.id, "device.import", d.id, { channels: created.map((x) => x.channel) });
  const rows = await Promise.all(created.map((cam) => c.env.DB.prepare("SELECT * FROM cameras WHERE id = ?").bind(cam.id).first<CameraRow>()));
  return c.json(await Promise.all(rows.filter(Boolean).map((r) => cameraOut(c.env, r!, user))));
});
