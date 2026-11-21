# CameraScope

CameraScope is a web app for finding public cameras near you and watching cameras you own, on one map-based dashboard.

It keeps two kinds of camera strictly apart:

| | **Public feeds** (blue) | **Private feeds** (amber) |
|---|---|---|
| What | Traffic cameras, webcams and IP cameras whose operators **publish them openly** | IP cameras you **own** or are **explicitly authorised** to view |
| Where they come from | Official sources: Caltrans, NYC DOT, OpenStreetMap, Windy, a curated seed list | Added by you, with connection details you are authorised to use |
| Who can see them | Every signed-in user | Only the owner. Other users get `404` |
| Credentials | None | Encrypted with AES-256-GCM and never returned by the API |
| Database table | `public_cameras` | `private_cameras` (always filtered by `owner_id`) |

CameraScope **never** scans for exposed devices, guesses default passwords, exploits vulnerabilities or bypasses authentication. See [docs/SECURITY.md](docs/SECURITY.md).

---

## Features

- **Location-based discovery.** The browser asks for permission to use your location. You can search a radius from 1 to 200 km and filter by camera type. Each camera shows its location, type, availability and source. **Scan directories** runs a live OpenStreetMap/Windy query for the area you are looking at.
- **Interactive map.** Uses OpenStreetMap and Leaflet. Public and private markers have different colours, offline cameras are flagged, and the search radius is drawn on the map.
- **Live viewer.** Shows 1×1, 2×2 or 3×3 tiles with real-time playback, full-screen mode and a status light per tile (connecting, live, stalled or error).
  - Gateway streams use WebRTC (WHEP) first and fall back to HLS automatically.
  - Quality selection uses the main or sub stream for private cameras, and the HLS renditions for public feeds.
  - Snapshots are available only when the feed allows them. For public feeds this comes from the provider's licence. For private cameras the owner turns it on or off.
- **Private camera connections:**
  - **RTSP / RTSPS**
  - **ONVIF**: CameraScope queries the device for its profiles and picks the main and sub stream automatically.
  - **WebRTC (WHEP)**
  - **HLS**
  - **MJPEG**
  - **Manufacturer APIs**: Axis VAPIX, Hikvision ISAPI, Dahua, Amcrest and Reolink, using their documented stream and snapshot endpoints.

  A camera can be on your home network, behind an LTE router or at another site. If CameraScope can reach it, it works the same way.
- **Dashboard.** Includes the map, a discovery panel, the live viewer grid, **My cameras**, **Saved**, **Recently viewed** and **Connection diagnostics**. Diagnostics checks DNS and network policy, TCP, the RTSP DESCRIBE / ONVIF / HLS handshake and the gateway, and keeps a history of runs.

## Architecture

```
Browser ──HTTPS──► Caddy ──► Next.js frontend (React/TypeScript)
                     │
                     └─/api/*──► FastAPI backend ──► PostgreSQL
                                   │   ▲
                         control API│   │ authenticated HLS/WHEP proxy (/api/media/*)
                                   ▼   │
                                 MediaMTX gateway ──RTSP/RTSPS/WHEP/HLS──► cameras
Browser ◄──────── WebRTC media (UDP 8189, DTLS-SRTP) ────────┘
```

- **Frontend** (`frontend/`): Next.js 14 (app router), React 18, TypeScript, Leaflet and hls.js.
- **Backend** (`backend/`): Python FastAPI with async SQLAlchemy and asyncpg, argon2id password hashing, JWT sessions in HttpOnly cookies, and AES-GCM encryption for camera credentials.
- **Gateway** (`mediamtx/`): [MediaMTX](https://github.com/bluenviron/mediamtx). It pulls camera streams only while someone is watching and repackages them for the browser without transcoding. Its HTTP ports are never published. Every HLS segment and WHEP negotiation goes through the backend, which checks ownership.
- **Database** (`database/schema.sql`): PostgreSQL 16. The backend also creates the tables on startup.

## Installation

### Option A: Docker (recommended)

Requirements: Docker 24+ with Compose v2.

```bash
cd camerascope
./scripts/generate-secrets.sh      # creates .env with random secrets (mode 600)
# edit .env: SITE_ADDRESS (your domain or localhost) and PUBLIC_HOST (public IP / DNS)
docker compose up -d --build
```

Then open **https://localhost**, or your `SITE_ADDRESS`, and create an account. The **first account created becomes the administrator.**

- With `SITE_ADDRESS=localhost`, Caddy uses its own local certificate authority, so the browser will show a certificate warning. To trust it, run `docker compose exec caddy caddy trust`, or import `/data/caddy/pki/authorities/local/root.crt` from the `caddy_data` volume.
- With a real domain, Caddy gets a Let's Encrypt certificate automatically. Open ports **80/tcp** and **443/tcp** for that.
- WebRTC needs **UDP 8189** open, and `PUBLIC_HOST` set to the address browsers use to reach the server. Without it, playback still works over HLS through 443.

**Try the full pipeline with a simulated camera:**

```bash
# also enable the demo public feed
sed -i 's/^PROVIDERS_ENABLED=.*/PROVIDERS_ENABLED=seed,caltrans,nyc_dot,osm,demo/' .env
docker compose --profile demo up -d --build
```

This starts a `demo-camera` container that serves an RTSP test pattern.

1. The demo public feed appears in San Francisco (Civic Center).
2. To test a private camera, go to **My cameras → Add**:
   - **Type:** RTSP
   - **Host:** `demo-camera`
   - **Port:** `8554`
   - **Main stream path:** `/private`
   - **Sub stream path:** `/private_sub`
   - **Username / password:** `demo` / `demo-Passw0rd`
   - Tick the authorisation box.
3. Open the camera and switch between High and Low quality, take a snapshot or run diagnostics.

### Option B: Local development

Requirements: Python 3.12+, Node 20+, PostgreSQL 16, and optionally a MediaMTX binary.

```bash
# database
createuser -P camerascope && createdb -O camerascope camerascope

# backend
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export JWT_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
export CREDENTIAL_KEYS="k1:$(python -c 'import os,base64;print(base64.b64encode(os.urandom(32)).decode())')"
export DATABASE_URL=postgresql+asyncpg://camerascope:<password>@localhost:5432/camerascope
export COOKIE_SECURE=false            # plain http in dev only
uvicorn app.main:app --reload --port 8000

# gateway (optional, needed for RTSP/ONVIF/WebRTC playback)
./mediamtx ../mediamtx/mediamtx.yml

# frontend (proxies /api to BACKEND_URL)
cd ../frontend
npm install
BACKEND_URL=http://localhost:8000 npm run dev     # http://localhost:3000
```

### Tests

```bash
cd backend
createdb -O camerascope camerascope_test
DATABASE_URL=postgresql+asyncpg://camerascope:<pw>@localhost:5432/camerascope_test pytest -q
cd ../frontend && npm run typecheck && npm run build
```

The backend test suite (28 tests) covers:

- registration, login and lockout
- CSRF rejection of cross-site requests
- encryption and swap-resistance of stored credentials
- the SSRF guard (metadata service, loopback, internal hosts, private ranges)
- owner isolation of private cameras and media paths
- vendor URL presets and ONVIF host pinning
- the RTSP digest handshake against a fake camera, including the rule that each run makes exactly one authentication attempt and the camera locks after repeated failures

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `JWT_SECRET` | (required) | Signs session tokens. Use at least 32 random characters. |
| `CREDENTIAL_KEYS` | (required) | A list of `kid:base64key` entries. The first key encrypts and every key can decrypt, which allows key rotation. |
| `DATABASE_URL` | local | SQLAlchemy async URL. |
| `ALLOW_PRIVATE_NETWORK_TARGETS` | `true` | Lets you add cameras on LAN addresses. **Set this to `false` for multi-tenant hosting.** |
| `PROVIDERS_ENABLED` | `seed,caltrans,nyc_dot,osm` | Public camera sources. You can also add `windy` and `demo`. |
| `CALTRANS_DISTRICTS` | `3,4,7,11,12` | Which Caltrans districts to import. |
| `WINDY_API_KEY` | empty | Enables the Windy webcams provider. |
| `PROVIDER_SYNC_INTERVAL_HOURS` | `12` | How often bulk providers are refreshed. |
| `COOKIE_SECURE` | `true` | Set to `false` only for local plain-HTTP development. |
| `SITE_ADDRESS` / `PUBLIC_HOST` | `localhost` | Caddy hostname / the WebRTC address browsers use. |

## Documentation

- [docs/API.md](docs/API.md): REST API reference. Interactive OpenAPI docs are at `/api/docs`.
- [docs/SECURITY.md](docs/SECURITY.md): security architecture, threat model and operator checklist.
- [database/schema.sql](database/schema.sql): PostgreSQL schema. Regenerate it with `python -m scripts.dump_schema`.

## Project layout

```
camerascope/
├── backend/            FastAPI app (app/), tests/, Dockerfile
│   └── app/
│       ├── routers/    auth, public, private, media (gateway proxy), library (saved/history/diagnostics)
│       └── services/   netguard (SSRF), onvif, rtsp, mediamtx, sources (vendor presets), providers/
├── frontend/           Next.js app (src/app, src/components, src/lib), Dockerfile
├── database/           schema.sql
├── mediamtx/           gateway config + demo camera config
├── docs/               API.md, SECURITY.md
├── scripts/            generate-secrets.sh
├── Caddyfile
└── docker-compose.yml
```

## Limitations and notes

- **How public feeds play.**
  - Public HLS feeds, such as Caltrans, play directly from the operator's HTTPS CDN.
  - Still images and MJPEG are proxied through the backend. This avoids mixed-content and CORS problems and means the operator never sees viewers' IP addresses.
  - Feeds that the operator only publishes on its own web page open as an external link and are not embedded.
- **Browser codecs.** WebRTC and HLS play only codecs the browser supports. H.264 works everywhere. H.265/HEVC cameras play only in browsers with HEVC support, so use the camera's H.264 sub stream as the "Low" quality if needed.
- **Scaling.** The rate limiter is in-process, which is fine for one backend replica. Use Redis if you scale horizontally.
