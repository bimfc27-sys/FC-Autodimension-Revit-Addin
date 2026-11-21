# CameraScope REST API

The base path is `/api`. All bodies are JSON unless noted otherwise. The interactive OpenAPI UI is at **`/api/docs`**, ReDoc at `/api/redoc`, and the raw schema at `/api/openapi.json`.

## Conventions

- **Authentication.** Sign-in sets a session cookie named `cs_session`. It is HttpOnly, Secure and SameSite=Strict and lasts 12 hours. Non-browser clients can send the same token as `Authorization: Bearer <token>`.
- **Errors.** Errors come back as `{"detail": "..."}` with a standard status code:

  | Status | Meaning |
  |---|---|
  | `400` | Blocked by network policy |
  | `401` | Not signed in |
  | `403` | Forbidden, or snapshot not permitted |
  | `404` | Not found. Also returned for another user's private camera, so the API never confirms that it exists. |
  | `409` | Camera not configured |
  | `422` | Validation error |
  | `423` | Camera locked after repeated authentication failures |
  | `429` | Rate limited. The response includes a `Retry-After` header. |
  | `502` / `504` | Upstream camera or gateway error |
- **CSRF.** The API rejects state-changing requests whose `Sec-Fetch-Site` header is `cross-site`.
- **Camera kinds.** Every camera object has a `kind` field, either `"public"` or `"private"`.

---

## Auth

| Method | Path | Body | Description |
|---|---|---|---|
| POST | `/auth/register` | `{email, password, display_name}` | Creates an account and signs in. The password must have at least 12 characters and mix 3 of: lowercase, uppercase, digits, symbols. The first account created becomes the administrator. |
| POST | `/auth/login` | `{email, password}` | Signs in. After 5 failures the account is locked for 15 minutes. The error message is the same whether or not the e-mail exists. |
| POST | `/auth/logout` | – | Clears the session cookie. |
| POST | `/auth/logout-all` | – | Revokes **all** of the user's sessions by incrementing the token version. |
| GET | `/auth/me` | – | Returns the current user: `{id, email, display_name, is_admin}`. |

## Public cameras

| Method | Path | Description |
|---|---|---|
| GET | `/public-cameras/nearby?lat=&lon=&radius_km=25&camera_type=&limit=300` | Public cameras inside the radius, sorted by distance. The maximum radius is 500 km. `camera_type` is one of `traffic`, `webcam` or `public_ip`. No authentication is needed. If you are signed in, each camera's `saved` flag is filled in. |
| POST | `/public-cameras/discover` | Body: `{lat, lon, radius_km<=50}`. Queries the on-demand directories (OpenStreetMap, Windy) for that area, imports what they return, and returns `{imported, errors, cameras}`. Limited to 6 calls per 5 minutes. |
| GET | `/public-cameras/{id}` | One camera. |
| GET | `/public-cameras/{id}/playback` | Returns a playback descriptor (see below). Requires sign-in. |
| GET | `/public-cameras/{id}/image` | The latest still image, fetched through the SSRF-guarded proxy. |
| GET | `/public-cameras/{id}/snapshot` | Downloads the still as an attachment. Returns `403` unless `snapshot_allowed` is true. |
| GET | `/public-cameras/{id}/mjpeg` | MJPEG stream proxy. |

**PublicCamera**

```json
{
  "kind": "public", "id": "uuid", "provider": "caltrans", "name": "I-5 at Main St",
  "description": "I-5 NB Los Angeles", "camera_type": "traffic",
  "latitude": 34.05, "longitude": -118.24, "stream_type": "hls",
  "page_url": null, "source_name": "Caltrans CWWP2", "source_url": "https://cwwp2.dot.ca.gov/",
  "license": "California public data - see source terms", "snapshot_allowed": true,
  "status": "online", "last_checked_at": "2026-01-01T00:00:00Z", "distance_km": 1.42, "saved": false
}
```

`stream_type` is one of:

| Value | What it is |
|---|---|
| `hls` | The operator's HLS stream. |
| `image` | A still image that is refreshed regularly. |
| `mjpeg` | A multipart JPEG stream. |
| `webpage` | The feed is only on the operator's website (external link). |
| `gateway` | Relayed through MediaMTX. |

## Private cameras (owner only)

| Method | Path | Description |
|---|---|---|
| GET | `/cameras` | Lists your cameras. |
| POST | `/cameras` | Adds a camera. See the body below. `authorization_attested` must be `true`. |
| GET | `/cameras/{id}` | Gets one camera. |
| PATCH | `/cameras/{id}` | Partial update. Sending new `credentials` resets the authentication-failure lock. |
| DELETE | `/cameras/{id}` | Deletes the camera, its encrypted credentials and its gateway paths. |
| GET | `/cameras/vendors` | The supported manufacturer presets. |
| POST | `/cameras/onvif/probe` | Body: `{host, port, use_tls, credentials:{username,password}, authorization_attested:true}`. Returns `{manufacturer, model, firmware, profiles:[{token,name,encoding,width,height,stream_uri,snapshot_uri}]}`. Credentials are removed from the returned URIs. Limited to 3 calls per host per 10 minutes. |
| POST | `/cameras/{id}/onvif/refresh` | Asks the device for its stream URIs again. |
| GET | `/cameras/{id}/playback?quality=high\|low` | Registers the gateway path and returns a playback descriptor. |
| GET | `/cameras/{id}/mjpeg` | MJPEG proxy for MJPEG cameras. |
| GET | `/cameras/{id}/snapshot` | Downloads a JPEG from the device's snapshot endpoint. Requires snapshots to be allowed for the camera. |

**Create body**

```json
{
  "name": "Front door",
  "protocol": "rtsp",                 // rtsp | onvif | webrtc | hls | mjpeg | vendor_api
  "vendor": "generic",                // generic | axis | hikvision | dahua | amcrest | reolink (vendor_api)
  "host": "cam.example.com",          // hostname or IP only — no scheme, path or credentials
  "port": 554,
  "use_tls": false,                   // RTSPS / HTTPS
  "config": {
    "main_path": "/stream1", "sub_path": "/stream2", "snapshot_path": null,
    "rtsp_transport": "tcp", "channel": 1, "snapshot_allowed": true,
    "onvif_main_profile": null, "onvif_sub_profile": null,
    "resolved_main_uri": null, "resolved_sub_uri": null, "resolved_snapshot_uri": null
  },
  "credentials": {"username": "viewer", "password": "••••"},
  "latitude": 40.7, "longitude": -73.9,
  "authorization_basis": "owner",     // owner | authorized_user
  "authorization_attested": true
}
```

**PrivateCamera response.** The password is never returned.

```json
{ "kind": "private", "id": "uuid", "name": "Front door", "protocol": "rtsp", "vendor": "generic",
  "host": "cam.example.com", "port": 554, "use_tls": false, "config": {...},
  "latitude": 40.7, "longitude": -73.9, "has_credentials": true, "username": "viewer",
  "authorization_basis": "owner", "authorization_attested_at": "...",
  "status": "online|offline|auth_failed|unknown", "last_error": null, "last_checked_at": "...", "created_at": "..." }
```

## Playback descriptor

```json
{
  "kind": "private", "camera_id": "uuid",
  "mode": "gateway",                    // gateway | hls | mjpeg | image | webpage
  "whep_url": "/api/media/prv-<hex>-high/whep",
  "hls_url": "/api/media/prv-<hex>-high/hls/index.m3u8",
  "image_url": null, "page_url": null, "refresh_seconds": null,
  "quality": "high", "qualities": [{"id":"high","label":"High (main stream)"},{"id":"low","label":"Low (sub stream)"}],
  "snapshot_allowed": true, "snapshot_url": "/api/cameras/<id>/snapshot", "attribution": null
}
```

How the client plays each `mode`:

| Mode | Client behaviour |
|---|---|
| `gateway` | Sends a WHEP offer to `whep_url` (`POST`, `Content-Type: application/sdp`, expects `201` plus a `Location` header). If that fails or no video arrives within 8 seconds, it plays `hls_url` instead. |
| `hls` | Plays `hls_url` directly from the operator's CDN. |
| `mjpeg` | Shows `image_url` in an `<img>` element. |
| `image` | Reloads `image_url` every `refresh_seconds`. |
| `webpage` | Links out to `page_url`. |

## Media gateway proxy

These endpoints require a session. For `prv-*` paths the backend checks that the caller owns the camera.

| Method | Path | Description |
|---|---|---|
| GET | `/media/{path}/hls/{file}` | LL-HLS playlists and segments, proxied from MediaMTX. |
| POST | `/media/{path}/whep` | WHEP SDP offer and answer. The `Location` header is rewritten to the proxy URL. |
| PATCH / DELETE | `/media/{path}/whep/{session}` | Trickle ICE and session teardown. |

## Library

| Method | Path | Description |
|---|---|---|
| GET | `/saved` | Your saved public cameras. |
| PUT / DELETE | `/saved/{public_camera_id}` | Save or unsave a camera. |
| POST | `/history` | Body: `{camera_kind, camera_id}`. Records a view. Private cameras must be yours. |
| GET | `/history` | The last 20 distinct cameras you viewed: `[{viewed_at, camera}]`. |
| DELETE | `/history` | Clears your history. History older than 90 days is pruned automatically. |

## Diagnostics

| Method | Path | Description |
|---|---|---|
| POST | `/diagnostics` | Body: `{camera_kind, camera_id}`. Runs the checks and returns `{id, ok, steps:[{name, ok, detail, duration_ms}]}`. Limited to 4 runs per camera per 5 minutes. Updates the camera's status. |
| GET | `/diagnostics` | Your last 25 runs. |

Checks for private cameras: configuration → DNS and network policy → TCP → protocol handshake (RTSP OPTIONS/DESCRIBE with **one** authentication attempt, ONVIF GetDeviceInformation, HLS playlist or MJPEG content type) → gateway path state → vendor snapshot endpoint.

## Providers and health

| Method | Path | Description |
|---|---|---|
| GET | `/providers` | Each provider's status: last run, success flag, number of cameras imported, message. |
| POST | `/admin/providers/{name}/sync` | Admin only. Refreshes a bulk provider (`seed`, `caltrans`, `nyc_dot`, `demo`) immediately. |
| GET | `/health` | Returns `{status, gateway}`, where `gateway` is true if the MediaMTX API is reachable. |

## Example: curl

```bash
B=https://localhost
curl -k -c jar -H 'Content-Type: application/json' -d '{"email":"me@example.com","password":"Sup3r-Secret-pass","display_name":"Me"}' $B/api/auth/register
curl -k -b jar "$B/api/public-cameras/nearby?lat=34.05&lon=-118.24&radius_km=10" | jq '.[0]'
curl -k -b jar -H 'Content-Type: application/json' -d @camera.json $B/api/cameras
curl -k -b jar "$B/api/cameras/<id>/playback?quality=low"
```
