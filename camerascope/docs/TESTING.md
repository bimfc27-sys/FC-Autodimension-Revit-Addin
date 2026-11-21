# Testing CameraScope

## 1. Automated backend tests

```bash
cd backend && pytest -q     # needs a PostgreSQL test database, see README
```

There are 35 tests. They run against the real Alembic migrations and cover:

- authentication, lockout and CSRF
- AES-GCM credential encryption, key rotation and row binding
- the SSRF guard
- isolation of private cameras and media paths
- the RTSP digest probe against a fake camera, including one authentication attempt per run and lockout after 3 failures
- building roles: viewer vs manager, hidden connection details, revocation
- groups and favorites
- single-use gateway enrollment and gateway ownership
- the gateway agent's `ALLOWED_SUBNETS` / `ALLOWED_PORTS` policy
- ONVIF NVR channel grouping

Migrations were also checked separately:

- On a fresh database, `alembic check` reports that the migrations match the models.
- A 1.0 database (created from the old `schema.sql`) is stamped at the baseline and upgraded, and its saved cameras become favorites.
- Downgrading to the baseline works.

## 2. Multi-network streaming lab (`backend/tests/lab/`)

The lab reproduces the three camera configurations with **real software end to end**. The only simulated parts are the cameras themselves: MediaMTX + ffmpeg producing an H.264 test pattern over RTSP with authentication, and the ONVIF responder of the NVR.

| Network (Linux network namespace) | Contents | How CameraScope reaches it |
|---|---|---|
| `lan1` 10.10.1.0/24 | RTSP camera, main 1280×720 and sub 480×270 | **Direct / VPN**: routed from the cloud, like a VPN or same-LAN install |
| `site2` 192.168.50.0/24 | RTSP camera, plus **gateway A** (agent + MediaMTX) | **Gateway tunnel only**. iptables drops every new connection from the cloud into this network (checked: a direct `ffprobe` from the cloud times out). |
| `site3` 192.168.60.0/24 | **2-channel ONVIF NVR**, each channel with main and sub stream, plus **gateway B** | **Gateway tunnel only** (isolated the same way) |
| Docker bridge | **Gateway C** deployed with `gateway/docker-compose.yml` (built agent image + `bluenviron/mediamtx:1.9.3`) reaching the `lan1` camera | Gateway tunnel |

The cloud side ran the real backend (uvicorn), the real cloud MediaMTX (`mediamtx/mediamtx.yml`), Caddy with TLS (internal CA, so `wss://` and `https://` with certificate verification by the agents), PostgreSQL 16 and the production Next.js build. Run it with `lab.sh up`, `e2e_api.py`, `browser.js`, `reconnect.js` and `viewer.js`.

### Results (recorded 2026-11-21)

| Check | Result |
|---|---|
| Gateways A, B and C enroll with one-time codes and connect over TLS | ✅ Online. Each reported its allowed subnets. |
| NVR channel discovery through gateway B (ONVIF) | ✅ 2 channels with main and sub profiles. No stream URIs sent to the client. |
| Import NVR channels as cameras | ✅ "NVR CH1" and "NVR CH2" via "Site 3 NVR network" |
| HLS through the whole chain, decoded with ffprobe: Caddy → backend proxy → cloud MediaMTX → tunnel → gateway MediaMTX → camera | ✅ All 4 lab cameras plus the Docker-gateway camera, HD and SD: H.264 1280×720 / 480×270 / 640×360, 45 frames decoded in 3 s |
| **Browser playback** (Chromium, desktop 1440×900) | ✅ 4 feeds at once (direct, gateway A, NVR CH1 and CH2 via gateway B): **WebRTC**, 1280×720, currentTime advancing |
| Quality switch in browser (HD → SD) | ✅ 1280×720 → 480×270 |
| Forced HLS fallback (WHEP blocked) | ✅ All 4 feeds play over HLS |
| **iPhone 13 emulation** (Chromium with the iPhone viewport, touch and user agent) | ✅ Same 4 feeds over WebRTC in the 2×2 phone layout. Bottom navigation works. |
| Layouts 1 / 4 / 9 | ✅ |
| Camera outside a gateway's `ALLOWED_SUBNETS` | ✅ Refused **by the gateway** ("outside ALLOWED_SUBNETS"), logged on the gateway as `REFUSED` |
| Gateway outage | ✅ Camera status becomes `gateway_offline`. Playback returns a clear 503. The open tile shows the error and a reconnect countdown. |
| Automatic recovery after the gateway restarts with stored credentials | ✅ The tile resumed live video with no user action about 6.5 s after the gateway was back |
| Building sharing (viewer on the phone layout) | ✅ The viewer sees "Building cameras", plays the gateway camera, sees no host, port, paths or username, and has no diagnostics |
| Secrets in the browser | ✅ No camera password, relay secret or `rtsp://` URL in any `/api/*` response or console message during any browser run |
| Diagnostics through a gateway | ✅ Gateway tunnel → reachability → RTSP handshake → streaming gateways |
| **Production Docker Compose stack** (`docker-compose.yml`, built images, `--profile demo`) | ✅ postgres, backend (migrations 0001→0002 at startup), frontend, cloud MediaMTX and Caddy HTTPS all up. The first account became admin. |
| Compose stack + **Dockerized gateway** (`gateway/docker-compose.yml`) enrolling against it by hostname | ✅ Connected over TLS (`CADDY_TLS=tls internal` for the lab hostname). Camera behind it plays. |
| Browser on the compose stack, desktop and iPhone emulation | ✅ "Demo camera (Direct)" 1920×1080 and "Living room (via gateway)" 1280×720, both **WebRTC**, playing |

## 3. Not tested here, and why

- **Real iPhone Safari and real Android Chrome devices.** The test environment has no physical phones, and Playwright's WebKit engine is not installed here. The phone checks above used Chromium emulation of an iPhone 13, which validates layout and touch but **not Safari's media stack**.
  - The player has the Safari-specific code paths: `playsinline`, muted autoplay, `webkitEnterFullscreen`, native HLS fallback and standard fMP4 HLS.
  - **Verify on your iPhone after deployment:** open a camera on Wi-Fi and on mobile data, switch HD/SD, and try full screen.
- **Your real cameras and NVRs.** These tests used simulated cameras (standard RTSP/H.264 and an ONVIF responder). Real devices can differ: H.265-only streams, vendor ONVIF quirks, UDP-only RTSP. Run **Diagnostics** on each camera after adding it.
- **A public HTTPS deployment.** This needs a server and a domain (see DEPLOYMENT.md). It was not available from the test environment.

## 4. Free home-PC deployment (`deploy/windows`)

See [FREE-DEPLOYMENT.md §8](FREE-DEPLOYMENT.md#8-what-was-verified-and-what-was-not). In summary:

- **Verified:**
  - the unmodified home compose stack behind an HTTPS proxy standing in for Tailscale Serve
  - registration closed after the first (admin) account, with stranger registration returning 403
  - the admin creating accounts
  - a LAN camera added by IP, username and password playing over WebRTC
  - HLS fallback and "HLS only" playing with the WebRTC port blocked (the public-tunnel case)
  - a gateway on an isolated network connecting through the HTTPS entry point, with its camera playing
- **Not verified:**
  - running the PowerShell scripts (no Windows or PowerShell available)
  - Tailscale or Cloudflare themselves
  - Docker Desktop on Windows
  - real iPhone Safari
  - real cameras
