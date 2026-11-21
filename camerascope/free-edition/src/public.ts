import { Hono } from "hono";
import { type AppEnv, type User, fail, now, rateLimit, requireUser, uuid } from "./core";

export interface PublicRow {
  id: string; provider: string; external_id: string; name: string; description: string | null; camera_type: string;
  latitude: number; longitude: number; stream_type: string; stream_url: string | null; snapshot_url: string | null;
  page_url: string | null; source_name: string; source_url: string | null; license: string | null;
  snapshot_allowed: number; status: string; updated_at: number;
}

export function publicOut(r: PublicRow, favs: Set<string>, dist: number | null = null) {
  return {
    kind: "public" as const, id: r.id, provider: r.provider, name: r.name, description: r.description, camera_type: r.camera_type,
    latitude: r.latitude, longitude: r.longitude, stream_type: r.stream_type, page_url: r.page_url, source_name: r.source_name,
    source_url: r.source_url, license: r.license, snapshot_allowed: !!r.snapshot_allowed, status: r.status,
    last_checked_at: r.updated_at ? new Date(r.updated_at * 1000).toISOString() : null,
    distance_km: dist === null ? null : Math.round(dist * 100) / 100, saved: favs.has(r.id),
  };
}

function haversine(a: number, b: number, c: number, d: number) {
  const R = 6371.0088, rad = Math.PI / 180;
  const x = Math.sin(((c - a) * rad) / 2) ** 2 + Math.cos(a * rad) * Math.cos(c * rad) * Math.sin(((d - b) * rad) / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(x));
}

async function favSet(db: D1Database, user: User | null): Promise<Set<string>> {
  if (!user) return new Set();
  const r = await db.prepare("SELECT camera_id FROM favorites WHERE user_id = ? AND camera_kind = 'public'").bind(user.id).all<{ camera_id: string }>();
  return new Set((r.results ?? []).map((x) => x.camera_id));
}

async function nearby(db: D1Database, lat: number, lon: number, radius: number, type: string | null) {
  const dlat = radius / 111, dlon = radius / Math.max(1e-6, 111 * Math.cos((lat * Math.PI) / 180));
  let q = "SELECT * FROM public_cameras WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?";
  const args: unknown[] = [lat - dlat, lat + dlat, lon - dlon, lon + dlon];
  if (type) { q += " AND camera_type = ?"; args.push(type); }
  const rows = (await db.prepare(q + " LIMIT 2000").bind(...args).all<PublicRow>()).results ?? [];
  return rows.map((r) => [r, haversine(lat, lon, r.latitude, r.longitude)] as const).filter(([, d]) => d <= radius).sort((a, b) => a[1] - b[1]).slice(0, 300);
}

function num(v: string | undefined, lo: number, hi: number, name: string, dflt?: number): number {
  const n = v === undefined && dflt !== undefined ? dflt : Number(v);
  if (!Number.isFinite(n) || n < lo || n > hi) fail(422, `${name} out of range`);
  return n;
}

export const publicCams = new Hono<AppEnv>();

publicCams.get("/nearby", async (c) => {
  const lat = num(c.req.query("lat"), -90, 90, "lat"), lon = num(c.req.query("lon"), -180, 180, "lon");
  const radius = num(c.req.query("radius_km"), 0.1, 500, "radius_km", 25);
  const type = c.req.query("camera_type") ?? null;
  const favs = await favSet(c.env.DB, c.get("user"));
  return c.json((await nearby(c.env.DB, lat, lon, radius, type)).map(([r, d]) => publicOut(r, favs, d)));
});

/** Live query of OpenStreetMap features tagged with a public webcam URL. */
publicCams.post("/discover", async (c) => {
  const user = requireUser(c);
  await rateLimit(c.env, `discover:${user.id}`, 6, 300);
  const b = (await c.req.json().catch(() => ({}))) as Record<string, unknown>;
  const lat = num(String(b.lat), -90, 90, "lat"), lon = num(String(b.lon), -180, 180, "lon"), radius = num(String(b.radius_km ?? 10), 0.1, 50, "radius_km");
  const r = Math.round(radius * 1000);
  const q = `[out:json][timeout:25];(nwr["contact:webcam"](around:${r},${lat},${lon});nwr["webcam"](around:${r},${lat},${lon}););out center 200;`;
  const imported: Record<string, number> = { osm: 0 };
  const errors: Record<string, string> = {};
  try {
    const resp = await fetch("https://overpass-api.de/api/interpreter", { method: "POST", body: new URLSearchParams({ data: q }), headers: { "User-Agent": "CameraScope-Free/1.0" } });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = (await resp.json()) as { elements?: { type: string; id: number; lat?: number; lon?: number; center?: { lat: number; lon: number }; tags?: Record<string, string> }[] };
    const stmts = [];
    for (const el of data.elements ?? []) {
      const tags = el.tags ?? {};
      let url = (tags["contact:webcam"] ?? tags.webcam ?? "").split(";")[0].trim();
      const la = el.lat ?? el.center?.lat, lo = el.lon ?? el.center?.lon;
      if (!url.startsWith("https://") && !url.startsWith("http://")) continue;
      if (la === undefined || lo === undefined) continue;
      if (url.length > 1000) url = url.slice(0, 1000);
      const isImg = /\.(jpe?g|png|webp)(\?|$)/i.test(url);
      stmts.push(c.env.DB.prepare(
        `INSERT INTO public_cameras (id, provider, external_id, name, description, camera_type, latitude, longitude, stream_type, snapshot_url, page_url, source_name, source_url, license, snapshot_allowed, status, updated_at)
         VALUES (?, 'osm', ?, ?, NULL, 'webcam', ?, ?, ?, ?, ?, 'OpenStreetMap', ?, 'Location data © OpenStreetMap contributors (ODbL); feed terms set by its operator', 0, 'unknown', ?)
         ON CONFLICT(provider, external_id) DO UPDATE SET name=excluded.name, latitude=excluded.latitude, longitude=excluded.longitude, stream_type=excluded.stream_type, snapshot_url=excluded.snapshot_url, page_url=excluded.page_url, updated_at=excluded.updated_at`,
      ).bind(uuid(), `${el.type}/${el.id}`, (tags.name ?? tags.description ?? "Public webcam").slice(0, 300), la, lo,
        isImg && url.startsWith("https://") ? "image" : "webpage", isImg && url.startsWith("https://") ? url : null, isImg ? null : url,
        `https://www.openstreetmap.org/${el.type}/${el.id}`, now()));
    }
    if (stmts.length) await c.env.DB.batch(stmts.slice(0, 200));
    imported.osm = Math.min(stmts.length, 200);
  } catch (e) {
    errors.osm = (e as Error).message;
  }
  const favs = await favSet(c.env.DB, user);
  return c.json({ imported, errors, cameras: (await nearby(c.env.DB, lat, lon, radius, null)).map(([r, d]) => publicOut(r, favs, d)) });
});

async function getPublic(db: D1Database, id: string) {
  const r = await db.prepare("SELECT * FROM public_cameras WHERE id = ?").bind(id).first<PublicRow>();
  if (!r) fail(404, "Camera not found");
  return r;
}

publicCams.get("/:id", async (c) => c.json(publicOut(await getPublic(c.env.DB, c.req.param("id")), await favSet(c.env.DB, c.get("user")))));

publicCams.get("/:id/playback", async (c) => {
  requireUser(c);
  const r = await getPublic(c.env.DB, c.req.param("id"));
  const base = { kind: "public", camera_id: r.id, hls_url: null, whep_url: null, image_url: null, refresh_seconds: null, quality: "auto", qualities: [],
    snapshot_allowed: !!r.snapshot_allowed, snapshot_url: null, page_url: r.page_url, attribution: `${r.source_name}${r.license ? ` - ${r.license}` : ""}` };
  if (r.stream_type === "hls" && r.stream_url?.startsWith("https://")) return c.json({ ...base, mode: "hls", hls_url: r.stream_url }); // played from the operator's CDN
  if (r.stream_type === "image" && r.snapshot_url) return c.json({ ...base, mode: "image", image_url: `/api/public-cameras/${r.id}/image`, refresh_seconds: 10 });
  // Continuous video (MJPEG) is not proxied through Cloudflare in the Free Edition: link to the operator instead.
  return c.json({ ...base, mode: "webpage", page_url: r.page_url ?? r.source_url });
});

publicCams.get("/:id/image", async (c) => {
  requireUser(c);
  const r = await getPublic(c.env.DB, c.req.param("id"));
  if (r.stream_type !== "image" || !r.snapshot_url?.startsWith("https://")) fail(404, "No still image for this camera");
  const resp = await fetch(r.snapshot_url, { headers: { "User-Agent": "CameraScope-Free/1.0" }, redirect: "follow" });
  const ct = resp.headers.get("content-type") ?? "";
  if (!resp.ok || !ct.startsWith("image/")) fail(502, `Operator returned ${resp.status} ${ct}`);
  const len = Number(resp.headers.get("content-length") ?? 0);
  if (len > 6 * 1024 * 1024) fail(502, "Image too large");
  return new Response(resp.body, { headers: { "Content-Type": ct, "Cache-Control": "private, max-age=5" } });
});
