# CameraScope Free Edition (iPhone-only, Cloudflare free services)

| | |
|---|---|
| Frontend | Cloudflare **Pages**: installable PWA at `https://<project>.pages.dev` |
| API | **Pages Functions** (Workers runtime), same origin: `free-edition/src` |
| Database | **D1**: `free-edition/migrations` |
| Cost | €0. Cloudflare free plan only. No VPS, domain, gateway, PC or manufacturer developer account. |
| Other editions | The self-hosted Docker edition and the Windows home-PC edition are unchanged and separate. They keep live WebRTC/HLS video, gateways and LAN cameras. |

## 1. What the Free Edition can and cannot do

**It can:**

- Run a private, password-protected dashboard reachable from anywhere over 4G/5G.
- Store camera and recorder credentials encrypted with AES-256-GCM. They never reach the browser.
- Show a **verified snapshot view**: real JPEG images, refreshed every 2–60 s (default 3 s), from cameras and recorders that are **reachable from the internet** and expose a **documented** HTTP snapshot endpoint.
- Keep favorites, groups, buildings with viewer/manager roles, history, diagnostics and an audit log.

**It cannot:**

- **Show live video.** Cloudflare's free services cannot convert RTSP into something Safari plays, and their terms restrict proxying video. The snapshot view is labelled as such everywhere and never presented as live video.
- **Reach LAN-only cameras.** An address like `192.168.x.x`, or a camera with no router port-forward, is shown as *"Not reachable from the internet"* with that reason.
- **Use manufacturer clouds** (EZVIZ, Imou, Tapo, Smart Life). They need developer accounts, which you excluded, or have no public API.

Video is switched on for a camera **only after** the server has received a real image from it. If a camera goes offline, its tile shows an error and the reason. It never shows a stale picture as if it were live.

## 2. Brand compatibility

| Brand | What the Free Edition uses | Video in Free Edition | Channel discovery | Tested against |
|---|---|---|---|---|
| Hikvision camera | ISAPI `/ISAPI/Streaming/channels/<ch>01/picture`, digest auth | Snapshot view (after verification) | — | simulated Hikvision (local workerd) |
| Hikvision NVR/DVR | same, per channel | Snapshot view per channel | ✅ ISAPI `InputProxy` / `Video/inputs` | simulated NVR (3 channels) |
| Dahua camera | `/cgi-bin/snapshot.cgi?channel=N`, digest auth | Snapshot view | — | simulated Dahua |
| Dahua NVR/XVR | same, per channel | Snapshot view per channel | ✅ `configManager ChannelTitle` | simulated XVR (2 channels) |
| Reolink camera/NVR | documented HTTP API `cmd=Snap` (credentials sent server-side; use HTTPS) | Snapshot view | manual channels | simulated Reolink |
| Uniview | the snapshot path documented for **your model**, which you enter | Snapshot view if verified | manual channels | not tested (no default path built in) |
| EZVIZ | — (cloud API needs a developer account; LAN-only RTSP) | ❌ none: registration, metadata and reachability only | — | behaviour tested ("No video" + reason) |
| Imou | — (cloud API needs a developer account) | ❌ none. If your model documents an HTTP snapshot URL, use "Other / ONVIF". | — | behaviour tested |
| TP-Link Tapo | — (no documented HTTP snapshot or cloud API) | ❌ none | — | behaviour tested |
| Smart Life / Tuya | — (developer account required) | ❌ none | — | behaviour tested |
| Other / ONVIF | your documented snapshot URL | Snapshot view if verified | manual channels | covered by the generic path |

**No real camera or recorder has been tested yet.** All snapshot tests used simulators that implement the documented endpoints, including real HTTP digest authentication. Run **Diagnostics** on each of your devices. It shows step by step whether the address is public, whether Cloudflare can reach it, whether the login succeeds, and whether an image arrives.

## 3. Requirements for your cameras (done from the iPhone)

For each network with cameras:

1. **Public address.** Use your router's public IP, or better a free **DDNS** name. Many routers and NVRs include one (for example Hik-Connect/Dahua DDNS, ASUS or FRITZ!Box DDNS).
2. **Router port-forward** from an external port, preferably non-default and above 10000, to the camera's or NVR's **HTTP or HTTPS** port. Most routers have an iPhone app or a web page for this.
3. **Security before exposing anything:**
   - update the device firmware
   - use a long, unique password
   - prefer HTTPS (the device needs a certificate Cloudflare trusts; self-signed certificates fail verification and Diagnostics says so)
   - create a **separate low-privilege user** on the camera for CameraScope
4. If your internet provider uses CGNAT (no public IPv4 address), port-forwarding cannot work, and those cameras cannot be used with the Free Edition.

## 4. iPhone-only setup (no token, no commands)

Everything below is done in **Safari on your iPhone**. Cloudflare builds the app directly from this GitHub repository, so no API token is needed.

**A. Put CameraScope on Cloudflare (about 10 minutes, once):**

1. Open **dash.cloudflare.com** → **Sign up** (free; no payment method needed) → verify your e-mail.
2. **Workers & Pages** → **Create** → **Pages** tab → **Connect to Git** → **GitHub**. Sign in to GitHub and allow Cloudflare to access the repository **FC-Autodimension-Revit-Addin**. Select it.
3. Fill in the build settings:
   - **Production branch:** `claude/camerascope-web-app-22fc1t`
   - **Framework preset:** None
   - **Build command:** `bash scripts/build-frontend.sh`
   - **Build output directory:** `public`
   - **Root directory** (under *Advanced*): `camerascope/free-edition`

   Tap **Save and Deploy** and wait for the build to finish (2–4 minutes).
4. **Storage & Databases** → **D1** → **Create database** → name it `camerascope` → Create.
5. Back in your Pages project → **Settings** → **Bindings** → **Add** → **D1 database** → variable name **`DB`**, database `camerascope` → Save.
6. *(Recommended)* **Settings** → **Variables and Secrets** → **Add**:
   - type **Secret**, name **`CREDENTIAL_KEY`**
   - value: any random text of 40+ characters (the iPhone *Passwords* app can generate one)
   - Save
7. **Deployments** → on the latest deployment, **⋯ → Retry deployment**. Bindings and secrets apply to new deployments only.
8. Open **https://\<your-project\>.pages.dev**. The app creates its database tables by itself on first use.

**B. First use:**

1. **Register.** The first account becomes the administrator, and self-registration closes after that.
2. **Share → Add to Home Screen.** CameraScope now opens full-screen like an app.
3. If the login page says *"Setup incomplete"*, step A5 or A7 is missing.

**C. Adding devices:**

- **Cameras → + Add:**
  - choose the brand
  - enter the public IP or DDNS name, the forwarded port and the channel
  - enter the camera username and password
  - confirm that you are authorised

  CameraScope tests the camera immediately and shows each step.
- **Manage → DVR / NVR → Add:** add the recorder, then *Discover channels* (Hikvision, Dahua) or *Enter manually*.
- **Manage → Buildings:** share cameras with people. An administrator creates their logins under **Manage → Settings → Accounts**.

**Updates:** every push to the branch redeploys automatically.

**If login fails with Cloudflare error 1102** (CPU limit), add a variable `PBKDF2_ITERATIONS` = `50000` and retry the deployment. The default of 100,000 PBKDF2 iterations has not yet been measured against the free-plan CPU limit in production.

The command-line path (`scripts/deploy.sh` with an API token) still works for people who prefer it.

## 5. Security design

- **Passwords:** PBKDF2-SHA256, 100,000 iterations with a 16-byte salt. argon2 does not fit the Workers free CPU budget. After 5 failed logins the account is locked for 15 minutes, and logins are rate-limited per IP and per e-mail in D1.
- **Sessions:** server-side, stored in D1 as the SHA-256 of a random 256-bit token. The cookie is `__Host-`, HttpOnly, Secure and SameSite=Strict, lasts 12 h with sliding expiry, and *sign out everywhere* is supported.
- **CSRF:** SameSite=Strict, plus every state-changing request must come from the site's own Origin or Sec-Fetch-Site.
- **Credentials:** AES-256-GCM with associated data bound to the row id and owner. The key is a Pages secret (`CREDENTIAL_KEY`). They are never returned to the browser or written to logs.
- **Camera logins:** exactly **one** authentication attempt per check. After 3 rejections the camera is locked until you change its credentials. There is no password guessing and no scanning.
- **Network rules:** LAN, loopback, link-local and metadata addresses and local hostnames are refused with an explanation. Port 25 is blocked.
- **Privacy and auditing:** shared viewers never see a camera's address, username or settings. The audit log records logins, camera and recorder changes, views, snapshots and diagnostics.

## 6. Free-tier limits

- **Workers requests:** 100,000 per day. Each snapshot refresh is one request: at 3 s per tile that is 1,200 requests per tile-hour, so **about 80 tile-hours a day** in total, after normal API use.
- **D1:** 5 M rows read and 100 k rows written per day. Snapshot refreshes don't write to D1. Logins, changes, history and diagnostics do.
- **Mobile data:** about 60 kB per snapshot at 3 s is roughly **70 MB per tile-hour**.

## 7. Deployment report

| Item | Status |
|---|---|
| Code: API, D1 schema, PWA, deploy script | ✅ Done, pushed to the branch |
| Local verification on the real Workers runtime + local D1 | ✅ **40/40 API checks**, plus LAN-address rejection and persistence across restart |
| Browser verification (Chromium, iPhone 13 emulation) | ✅ Login, list chips, 4 verified snapshot tiles refreshing, honest "No video" tile, PWA manifest, icons and service worker |
| Deployment without a token (Cloudflare GitHub integration, §4) | ✅ Supported. The app creates its tables on first use; there's a fallback encryption key when no secret is set; and there's a clear "Setup incomplete" message when the database isn't connected. Tested locally from an **empty** database with **no secret**: 40/40 checks pass. |
| Production deployment and pages.dev URL | ⏳ Needs your free Cloudflare account (§4A). I cannot create accounts on your behalf. |
| Production checks (authentication, D1 persistence, snapshot pipeline from Cloudflare's edge) | ⏳ After deployment, each verified separately |
| Real iPhone Safari, real cameras | ⏳ Yours to try after deployment; Diagnostics explains any failure |

**Demo page:** a self-contained demo of this interface with simulated cameras is published as a private claude.ai artifact (link in the chat). It shows how the app behaves; it cannot contain your real cameras.
