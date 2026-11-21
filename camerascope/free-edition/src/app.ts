import { Hono } from "hono";
import { HTTPException } from "hono/http-exception";
import { auth } from "./auth";
import { cameras, diagnostics } from "./cameras";
import { type AppEnv, csrfMiddleware, sessionMiddleware } from "./core";
import { devices } from "./devices";
import { organize } from "./organize";
import { publicCams } from "./public";

export const app = new Hono<AppEnv>().basePath("/api");

app.use("*", async (c, next) => {
  await next();
  c.header("X-Content-Type-Options", "nosniff");
  c.header("Referrer-Policy", "no-referrer");
  c.header("X-Frame-Options", "DENY");
  if (!c.res.headers.get("Cache-Control")) c.header("Cache-Control", "no-store");
});
app.use("*", csrfMiddleware);
app.use("*", sessionMiddleware);

app.get("/health", async (c) => {
  const db = await c.env.DB.prepare("SELECT COUNT(*) AS n FROM users").first<{ n: number }>().then(() => "ok").catch((e: Error) => `error: ${e.message}`);
  return c.json({ status: db === "ok" ? "ok" : "degraded", edition: "free", database: db, gateway: false });
});

// The Free Edition streams nothing through Cloudflare: snapshots only (see docs).
app.get("/client-config", (c) => c.json({ edition: "free", streaming_mode: "hls", live_rtsp_video: false, gateways: false }));
app.get("/gateways", (c) => c.json([])); // Local Camera Gateways exist only in the self-hosted / home-PC editions

app.route("/auth", auth);
app.route("/cameras", cameras);
app.route("/devices", devices);
app.route("/diagnostics", diagnostics);
app.route("/public-cameras", publicCams);
app.route("/", organize);

app.notFound((c) => c.json({ detail: "Not found" }, 404));
app.onError((err, c) => {
  if (err instanceof HTTPException) return err.getResponse();
  console.error(err);
  return c.json({ detail: "Internal error" }, 500);
});
