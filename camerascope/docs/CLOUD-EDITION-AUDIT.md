# CameraScope Cloud Edition: feasibility audit

**Goal.** Run CameraScope on an iPhone only: no PC, gateway, VPS, domain or paid plan, using Cloudflare Pages + Workers + D1. Access must work from anywhere over 4G/5G and support authorized cameras and DVR/NVRs on several independent networks.

**Audit scope.** The current CameraScope source: about 8,300 lines across backend/app, gateway/agent and frontend/src, with 70 API routes. The platform and manufacturer facts below were checked against official documentation in November 2026. Items I could not confirm are marked **unverified**.

---

## 0. Bottom line

1. **Everything except getting video out of the camera networks** can run on Cloudflare's free services:
   - the dashboard PWA, login and accounts
   - encrypted credential storage
   - camera inventory, groups, favorites, buildings and roles, history, audit log
   - public camera discovery and the map
2. **Live video from ordinary IP cameras and NVRs (RTSP/ONVIF) cannot work without a device on the camera network** unless one of these is true:
   - The camera is connected to a **manufacturer cloud with an official API** that returns browser-playable streams. Realistic today for **EZVIZ / Hikvision-via-EZVIZ**, **Imou (Dahua consumer)**, **Tuya / Smart Life-based** cameras and **Google Nest**.
   - The camera or NVR is **reachable from the internet**, through port-forwarding or a public IP, **and** serves HLS, MJPEG or JPEG over HTTP(S). For RTSP-only cameras this gives **snapshots, not live video**.

   Workers cannot convert RTSP into HLS or WebRTC: there is no ffmpeg/MediaMTX, CPU is limited to 10 ms per request, and there is no UDP.
3. **Cameras that are only on a private LAN** (no vendor cloud, no port-forward) are **impossible** to view from a Cloudflare-only design. The iPhone cannot act as the gateway either: iOS suspends background apps and Safari cannot open RTSP or TCP sockets.
4. **Brands without an official API: Reolink, TP-Link Tapo, Arlo, Ring, Eufy, Wyze, Blink, Amcrest, UniFi Protect.** I found no official public live-video API for individuals, so their cloud streams cannot be used legitimately. Over the internet they fall back to case 2 or 3.

So the Cloud Edition only works with your cameras if your cameras fall into the supported rows of §4. **Which brands and models you own decides the outcome.**

---

## 1. Verified platform facts (free plans)

| Service | Free allowance / constraint | Impact on CameraScope |
|---|---|---|
| **Workers** | 100,000 requests/day. **10 ms CPU** per invocation; waiting on the network doesn't count. 50 external subrequests per invocation. | Fine for the API. Too little CPU for argon2id or for any video processing. Every proxied HLS segment or snapshot costs one request. |
| Workers `fetch()` | In production, **custom ports are ignored for HTTPS**. Community reports say ports other than 80/443 are disregarded. | Cameras on `:8000`, `:8443`, `:554` and similar can't be reached with `fetch()`. |
| Workers TCP `connect()` | Raw TCP to any port except 25. **Private IPs, localhost and Cloudflare IPs are disallowed.** | Can reach *public* camera addresses on any port. HTTP and digest authentication have to be written by hand over the socket. TLS goes through `startTls`, and its handling of self-signed certificates is **unverified**. Can never reach a LAN address. |
| **Pages** | Static hosting, free `*.pages.dev` HTTPS URL. | Hosts the PWA. A Next.js static export is needed: the current `rewrites` don't work there and are replaced by same-origin Pages Functions or a Worker route. |
| **D1** | 5 GB in total (500 MB per database), 5 M rows read/day, 100 k rows written/day. Queries fail once the limits are hit. | Ample for one user's inventory, audit log and history. |
| **Durable Objects** | Free plan: SQLite-backed objects, 100 k requests/day, WebSocket hibernation. | Could host the gateway tunnel hub, but there is no gateway to connect to it. Usable for rate limiting and session revocation. |
| **Cron Triggers** | Available on the free plan. | Reachability checks (TCP `connect()` to public camera addresses) and provider syncs. |
| **Realtime SFU / TURN** | 1,000 GB/month free, then $0.05/GB. | Relays WebRTC, but cameras speak RTSP, so it is of no help without a converter. Not needed for Nest WebRTC, which streams from Google directly. |
| **Stream** (RTMP/SRT ingest → HLS) | **Paid only.** | Excluded. |
| **Terms of service** | Video and large files through the CDN require specific paid services (Stream, Images, R2…). The Developer Platform terms say it "can be used to host content", but the video wording is ambiguous. | **Risk:** proxying continuous camera video through a free Worker may break the terms and could be limited. Snapshots and API calls are fine. Vendor-cloud HLS is fetched by Safari **directly from the vendor's CDN** and doesn't touch Cloudflare. |

---

## 2. Code audit: every existing feature, classified

Legend:

- **A** = runs entirely on Cloudflare's free services. *(A⚠ = only for cameras reachable from the internet, or with the caveat noted.)*
- **B** = needs a manufacturer cloud API.
- **C** = impossible without a device or network gateway.

| Area | Current implementation | Class | Cloud Edition notes |
|---|---|---|---|
| Accounts, sessions, lockout, CSRF, registration modes | `routers/auth.py`, `security.py`, `deps.py` | **A** | Port to a Worker. **argon2id is not possible** in 10 ms or 128 MB; use **PBKDF2-SHA256** through WebCrypto. Workers cap PBKDF2 at 100k iterations, and the CPU cost within the free plan is **unverified**. Rate limits move from memory to D1 or a Durable Object. Optional extra layer: Cloudflare Access (Zero Trust, free tier) in front of the site. |
| Credential encryption (AES-256-GCM bound to row id and owner id) | `security.py` | **A** | WebCrypto AES-GCM. The key is held as a Worker secret. Same design. |
| Role-based access, buildings, groups, favorites, history, audit log | `routers/organize.py`, `library.py`, `models.py` | **A** | D1 schema port (UUIDs as TEXT, JSON as TEXT). Alembic migrations become D1 migrations. |
| Public camera discovery (seed, Caltrans, NYC DOT, OSM, Windy) and nearby search | `services/providers/*`, `routers/public.py` | **A⚠** | Cron + `fetch()`. Caltrans district JSON files are large, so parsing them within 10 ms of CPU is **at risk**; chunk the work or limit districts. OSM and Windy queries are fine. |
| Public HLS feeds from operator CDNs | `Player.tsx` (direct) | **A** | Safari plays them directly, with no Worker in the path. |
| Public still image and MJPEG proxy | `routers/public.py` | **A⚠** | Images are fine. Continuous MJPEG through a Worker falls under the terms-of-service video risk. |
| Public "gateway"-relayed streams (demo camera) | MediaMTX | **C** | Hidden in the Cloud Edition. |
| Map (Leaflet + OSM tiles) | `MapView.tsx` | **A** | Static. |
| Private camera inventory and encrypted credentials, attestation | `routers/private.py` | **A** | Same API shape. |
| **Live view: RTSP / RTSPS / ONVIF / vendor RTSP presets / NVR channels** | MediaMTX pull → WebRTC/HLS | **C**, or **B** where the camera is in a supported vendor cloud | Workers cannot depacketize or remux RTP/H.264 (CPU, no UDP, no ffmpeg). Browsers cannot play RTSP. |
| Live view: camera serving **HLS** over the internet | MediaMTX today | **A⚠** | A Worker can proxy the playlist and segments, adding the authentication server-side. This costs about 1 request per second per open tile and carries the terms-of-service risk. Rare in practice. |
| Live view: camera serving **MJPEG** over the internet | `proxy_mjpeg` | **A⚠** | A streaming Worker response over `connect()` for custom ports. Same terms-of-service risk. |
| Live view: camera or NVR exposing **WHEP/WebRTC** publicly | MediaMTX today | **A⚠** | The Worker proxies only the SDP (credentials stay server-side), and media flows camera → iPhone directly. Very rare. |
| **Snapshots** (Hikvision ISAPI, Dahua or Amcrest `snapshot.cgi`, Axis `image.cgi`, Reolink `Snap`, ONVIF `GetSnapshotUri`) | `routers/private.py` | **A⚠** | Only for cameras reachable from the internet. Raw TCP + hand-written digest authentication + optional TLS. With 1–2 s refresh this gives a "near-live" view at about 0.5–1 request/s per tile. |
| ONVIF probe, profiles, stream URIs | `services/onvif.py` | **A⚠** / C | SOAP over `connect()` to a public address. The returned RTSP URIs are unplayable anyway (see live view). Impossible for LAN-only devices. |
| NVR/DVR channel discovery (ONVIF, Hikvision ISAPI, Dahua) | `services/nvr.py`, `routers/devices.py` | **A⚠** / **B** / C | Works for an internet-reachable recorder, but its channels then give snapshots, not live video. Vendor clouds list channels through their own APIs (EZVIZ, Imou). Impossible on a LAN. |
| Diagnostics (DNS, policy, TCP, RTSP DESCRIBE, HTTP probe) | `services/cameras.py`, `rtsp.py` | **A⚠** | TCP connect and RTSP DESCRIBE over `connect()` work for public addresses. Gateway steps are dropped. |
| Reachability and online status | `status_monitor` | **A⚠** / **B** | Cron + TCP connect for public addresses. Vendor clouds report online status. LAN-only cameras show **"Not reachable from the cloud"**, stated plainly. |
| Local Camera Gateways (tunnel hub, agent, forwarders, enrollment) | `routers/gateways.py`, `gateway_hub.py`, `gateway/` | **C** | Kept in the codebase, not shown in the Cloud Edition. A Durable Object could take over the hub if you ever add a device. |
| Authenticated media proxy `/api/media/*` (MediaMTX HLS/WHEP) | `routers/media.py` | **C** | Replaced by vendor stream URLs or a Worker HLS/MJPEG proxy (A⚠). |
| SSRF guard | `services/netguard.py` | **A** | Simpler: `connect()` already refuses private addresses. Keep the scheme and port allow-list and block Cloudflare's own ranges. |
| Frontend dashboard (Next.js, mobile-first, auto-reconnect player, 1/4/9 grid, HD/SD) | `frontend/` | **A** | Static export to Pages plus a PWA manifest and service worker (new). The player gains vendor HLS and Nest WebRTC sources. HD/SD maps to vendor stream qualities where offered. |
| Docker, Compose, Caddy, MediaMTX, Postgres, Alembic | infrastructure | replaced | Wrangler + D1 migrations. |

**Features that would be lost or downgraded** (none silently):

- Live video for RTSP/ONVIF cameras and NVR channels that are not in a supported vendor cloud. Internet-reachable ones get **snapshots only**; LAN-only ones get **nothing**.
- Low-latency WebRTC for RTSP cameras.
- Gateways and multi-network tunnels.
- The public "relayed" demo stream.

These stay available in the self-hosted and home-PC editions.

---

## 3. Manufacturer cloud APIs (category B, question D)

| Manufacturer / ecosystem | Official API usable by an individual? | Live video format for Safari | Cost / limits | Verdict |
|---|---|---|---|---|
| **EZVIZ** (and **Hikvision** devices added to an EZVIZ account) | Yes. EZVIZ Open Platform: developer appKey/appSecret, `POST /api/lapp/token/get`, region-specific API domain. | HLS (plus RTMP and FLV) according to EZVIZ. The exact live-address endpoint **is unverified** and needs a prototype. Encrypted devices need their verification code. | Free developer registration. Quotas and pricing **unverified**. | **Candidate**. Highest priority if you own EZVIZ or Hik devices. |
| **Imou** (Dahua consumer line) | Yes. Imou Open Platform: `bindDeviceLive` creates the live address, then `getLiveStreamInfo`. | HLS (plus FLV). | Developer account. Free quota **unverified**. | **Candidate**. |
| **Tuya / Smart Life** (many no-name brands) | Yes. Tuya Developer Platform, "IoT Video Live Stream" service. | HLS, RTSP, WebRTC. | **Free trial that has to be subscribed and periodically renewed.** Long-term free use for individuals is **unverified**. | **Conditional**. |
| **Google Nest** | Yes. Smart Device Management API. | WebRTC (newer models, sessions of 5 minutes, extendable) or RTSPS (older models, which need conversion, so **C**). | **One-time US$5 registration fee**, not a subscription. Conflicts with "no payment" unless you accept it. | Conditional (your call). WebRTC models only. |
| **Hikvision professional** (Hik-Connect, Hik-Partner Pro) | Partner programme for installers/integrators. I found no access for individuals (**unverified**). | — | — | Not usable now. |
| **Dahua professional** (DMSS / DoLynk) | Partner/enterprise. Individual access **unverified**. | — | — | Not usable now. |
| Reolink, TP-Link Tapo/Kasa, Arlo, Ring, Eufy, Wyze, Blink, Amcrest, Ubiquiti UniFi Protect | No official public live-video API found. | — | — | **Not supported through their clouds.** Unofficial or reverse-engineered APIs are excluded (terms and legitimacy). |
| Axis, Hanwha, Verkada, Cisco Meraki MV | Official APIs exist but are tied to enterprise products or paid licences. | HLS (Verkada, Meraki) | Paid | Excluded under "no paid". |

---

## 4. Compatibility matrix: what you would get on your iPhone

Status is always measured: vendor-reported, or a TCP check from the cloud. It is never inferred from location.

| Your camera / recorder situation | Live video | Snapshots | Online status | Add by IP/user/password | Credentials stay server-side | Cloudflare video cost/risk |
|---|---|---|---|---|---|---|
| EZVIZ or Hikvision device in an **EZVIZ** account | ✅ HLS from the EZVIZ CDN *(endpoint unverified)* | ✅ via API *(unverified)* | ✅ from the vendor | Via EZVIZ account + developer keys, not IP | ✅ (the Worker holds keys and tokens; the browser only gets a short-lived stream URL) | none (vendor CDN) |
| Imou / Dahua consumer device in an **Imou** account | ✅ HLS *(quota unverified)* | ✅ *(unverified)* | ✅ | Via Imou account | ✅ | none |
| Tuya / Smart Life camera | ⚠️ HLS while the free trial is active | ⚠️ | ✅ | Via Tuya project | ✅ | none |
| Google Nest (WebRTC models) | ⚠️ WebRTC, **US$5 one-time** | ❌ (no snapshot API for these) | ✅ | Google sign-in | ✅ | none |
| Google Nest (older RTSPS models) | ❌ (needs a converter) | ❌ | ✅ | — | — | — |
| Any camera or NVR **port-forwarded**, serving **HLS** over HTTPS | ✅ via Worker proxy | ✅ | ✅ TCP check | ✅ | ✅ | ~1 request/s per tile; **terms risk** |
| Port-forwarded, serving **MJPEG** | ✅ (MJPEG, no audio) via Worker | ✅ | ✅ | ✅ | ✅ | continuous; **terms risk** |
| Port-forwarded, **RTSP/ONVIF only**, with an HTTP snapshot endpoint (most Hikvision/Dahua/Axis/Reolink/Amcrest) | ❌ live. ✅ **refreshing snapshots** (about 1 frame per 1–2 s) | ✅ | ✅ (+ RTSP DESCRIBE diagnostic) | ✅ | ✅ | ~0.5–1 request/s per tile; images are fine |
| Port-forwarded **NVR/DVR** (ISAPI/ONVIF/Dahua) | ❌ live. ✅ per-channel snapshots | ✅ | ✅ | ✅ (+ channel discovery) | ✅ | as above |
| Camera exposing **WHEP/WebRTC** publicly | ✅ WebRTC (media flows direct to the iPhone) | depends | ✅ | ✅ | ✅ (SDP proxied) | none |
| **LAN-only** camera or NVR (no port-forward, no vendor cloud) | ❌ **impossible** | ❌ | shows "not reachable from the cloud" | can be saved, but never connects | ✅ | — |
| Reolink / Tapo / Arlo / Ring / Eufy / Wyze cloud-only | ❌ (no official API) | ❌ | ❌ | — | — | — |

**About port-forwarding:**

- It puts the camera's login page and RTSP service on the internet. Many camera firmwares have had serious vulnerabilities.
- Only do it with current firmware, a strong unique password, HTTPS where the camera supports it, and ideally a non-default external port.
- Plain HTTP digest authentication avoids sending the password in clear, but the video and snapshots are still unencrypted on the internet.
- CameraScope will show a warning for `http://` and RTSP cameras added this way.

---

## 5. Free-tier limits and video bandwidth (question E)

**Worker request budget: 100,000 requests/day, shared by the API and any proxying.**

| Viewing mode | Worker requests per open tile | Hours/day within the free budget (all tiles together) |
|---|---|---|
| Vendor-cloud HLS or Nest WebRTC | about 1 per session (getting the stream URL) | effectively unlimited by Cloudflare (vendor quotas apply, **unverified**) |
| Worker-proxied HLS, 2 s segments | about 1/s (segment + playlist) → 3,600/h | **about 25 tile-hours/day**, for example 1 camera all day, or 4 cameras for about 6 h |
| Snapshot refresh every 2 s | 0.5/s → 1,800/h | about 50 tile-hours/day |
| Snapshot refresh every 1 s | 1/s | about 25 tile-hours/day |

**Mobile data on your iPhone,** for any architecture:

| Stream | Bitrate | Data per hour |
|---|---|---|
| Sub stream / SD (640×360) | 0.3–0.5 Mbit/s | about 0.15–0.25 GB |
| 1080p main stream | 2–4 Mbit/s | about 0.9–1.8 GB |
| 4K main stream | 6–8 Mbit/s | about 2.7–3.6 GB |
| JPEG snapshots, 1 per 2 s at ~60 kB | about 0.24 Mbit/s | about 0.1 GB |

A 2×2 grid of 1080p streams uses about 4–7 GB/hour on 4G/5G. Default to SD in grids.

**Upload at each camera site:** every remote viewer pulls a separate stream from that site's internet upload, unless a vendor cloud fans it out.

**Cost:** €0 on Cloudflare at this scale. Vendor quotas may cap usage (unverified). Nest costs a one-time US$5 if chosen.

---

## 6. What must be prototyped before building (do not skip)

1. **EZVIZ:** get a token, list devices, get the live HLS address, and play it in **real iPhone Safari** (encrypted and unencrypted devices). Record the quotas.
2. **Imou:** `bindDeviceLive` → `getLiveStreamInfo`, then play the HLS in Safari.
3. **Workers `connect()`:** reach a port-forwarded camera on a custom port, do HTTP digest authentication by hand, and use `startTls` against a self-signed camera certificate.
4. **PBKDF2 login** within the free CPU limit.
5. **Terms of service:** get Cloudflare's view on proxying small personal camera HLS or MJPEG volumes through Workers, or keep proxying limited to snapshots.

These spikes need **your real camera accounts and devices**. Simulated cameras cannot test vendor clouds.

## 7. Information needed from you

For each camera or recorder:

- **brand and model**
- **which app you use today** (EZVIZ, Hik-Connect, Imou Life, DMSS, Tapo, Reolink, Google Home, Smart Life, …)
- whether that network's router has **port-forwarding** set up, or could have it

This decides which rows of §4 apply to you. Nothing should be built until it shows that your cameras can actually be viewed.

---

Sources (checked November 2026):

- [Workers limits](https://developers.cloudflare.com/workers/platform/limits/)
- [Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/)
- [Workers TCP sockets](https://developers.cloudflare.com/workers/runtime-apis/tcp-sockets/)
- [Custom-port issue (cloudflare-docs #4299)](https://github.com/cloudflare/cloudflare-docs/issues/4299)
- [D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/)
- [D1 limits](https://developers.cloudflare.com/d1/platform/limits)
- [Durable Objects free tier](https://developers.cloudflare.com/changelog/2025-04-07-durable-objects-free-tier)
- [Realtime SFU pricing](https://developers.cloudflare.com/realtime/sfu/pricing)
- [Service-Specific Terms, Application Services](https://www.cloudflare.com/service-specific-terms-application-services)
- [Service-Specific Terms, Developer Platform](https://www.cloudflare.com/service-specific-terms-developer-platform/)
- [Delivering video with Cloudflare](https://developers.cloudflare.com/fundamentals/reference/policies-compliances/delivering-videos-with-cloudflare/index.md)
- [EZVIZ developer](https://www.ezviz.com/developer/index)
- [EZVIZ Open Platform SDK manual](https://open.ys7.com/doc/en/pcdoc/pc-sdk-en.html)
- [Imou Open Platform docs](https://open.imou.com/document/pages/b0e047/)
- [Imou HLS walkthrough (community)](https://juejin.cn/post/7669998887132069907)
- [Tuya IoT Video Live Stream](https://www.tuya.com/platform/clouddev/real-time-voice-and-video)
- [go2rtc Tuya notes](https://go2rtc.org/internal/tuya/)
- [Tuya support (free trial)](https://support.tuya.com/en/help/_detail/Kaoictqhip5xy)
- [Nest CameraLiveStream](https://developers.google.com/nest/device-access/traits/device/camera-live-stream)
- [Nest Device Access – Get started](https://developers.google.com/nest/device-access/get-started)
