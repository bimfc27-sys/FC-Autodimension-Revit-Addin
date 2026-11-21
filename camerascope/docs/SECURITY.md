# CameraScope security architecture

CameraScope lets people view camera feeds. That makes it a potential tool for privacy abuse, and a target for anyone trying to reach other people's devices or the server's own network. The design therefore rests on two commitments:

1. **Users only ever see feeds that are public by their operator's choice, or that the user owns or is authorised to view.**
2. **The server can never be used to attack cameras or internal infrastructure.**

## 1. What CameraScope will never do

| Prohibited behaviour | How it is prevented |
|---|---|
| Scanning IP ranges, searching services such as Shodan, or listing "exposed" cameras | Public cameras come **only** from official or opt-in directories: government DOT feeds, OpenStreetMap features tagged as webcams, Windy and a curated list. There is no code path that probes arbitrary addresses for discovery. |
| Guessing default or common passwords | Credentials are only ever the ones the owner typed. Every connection test makes **one** authentication attempt per protocol handshake and never retries with other values (the RTSP test checks this). There are no built-in default-credential lists. |
| Brute-forcing a camera through the platform | After **3** consecutive authentication failures the camera is **locked** (HTTP 423) until the owner enters new credentials. ONVIF probes are limited to 3 per host per 10 minutes, diagnostics to 4 per camera per 5 minutes, and camera creation to 30 per hour per user. |
| Exploiting vulnerabilities or bypassing authentication | Only standard, documented requests are made: RTSP OPTIONS/DESCRIBE, ONVIF GetDeviceInformation/GetProfiles/GetStreamUri/GetSnapshotUri, and the manufacturers' official stream and snapshot endpoints. No crafted payloads and no undocumented endpoints. |
| Viewing someone else's camera | Adding a private camera requires an explicit, timestamped attestation (`owner` or `authorized_user`). Every private resource is scoped by `owner_id`. Another user's camera returns **404**, so the API does not confirm it exists. |

## 2. Public and private separation

- **Separate tables.** `public_cameras` has no credential columns. `private_cameras` always has an `owner_id`, and every query filters on it through `get_owned_camera()`.
- **Separate gateway path names.** Paths are named `pub-<uuid>-<q>` or `prv-<uuid>-<q>`. The media proxy checks ownership before forwarding any HLS segment or WHEP exchange for `prv-*` paths.
- **Separate styling.** The UI marks public feeds in blue and private feeds in amber, on badges, tile borders and map markers. It also shows the operator's attribution and licence for public feeds.
- **Snapshots only where permitted.** For public feeds this follows each provider's licence: allowed for Caltrans and NYC DOT, not allowed for OSM and Windy. For private cameras the owner controls it. The server enforces this too: `/snapshot` returns 403.

## 3. Authentication and sessions

- **Passwords** are hashed with **argon2id** and transparently re-hashed when the parameters change. The minimum is 12 characters mixing 3 character classes. When an e-mail does not exist, the server still runs a hash so the response time does not reveal it.
- **Lockout.** After 5 failed logins the account is locked for 15 minutes. Login is also rate-limited per IP (20 per 5 minutes) and per e-mail (10 per 5 minutes). Error messages are the same either way.
- **Sessions** are HS256 JWTs carrying `sub`, `ver`, `exp`, `jti` and `typ`. They are stored in an **HttpOnly, Secure, SameSite=Strict** cookie. `POST /auth/logout-all` increments `token_version`, which revokes every session immediately.
- **CSRF** is blocked by SameSite=Strict, plus the server rejects state-changing requests marked `Sec-Fetch-Site: cross-site`. The API is same-origin, so CORS is off by default.
- **Authorisation.** Admin-only routes, such as provider sync, require `is_admin`. Only the first registered account is an admin.

## 4. Encrypting camera credentials

- **Algorithm:** AES-256-GCM via `cryptography`, with a 96-bit random nonce for each encryption.
- **Bound to the row.** The associated data is `camerascope:v1:<camera_id>:<owner_id>`. Copying one user's ciphertext onto another camera or owner fails authentication (tested).
- **Key rotation.** `CREDENTIAL_KEYS=newkid:key,oldkid:key`. The first key encrypts, every listed key can decrypt, and each row stores its key id. To rotate, put the new key first, then save each camera again through `PATCH` with its credentials.
- **Never returned.** The API returns only `has_credentials` and the username, never the password. Stream URIs are stored without credentials. Credentials are inserted only at the moment the gateway path is registered, and MediaMTX holds them in memory only (API path changes are not written to disk).
- **Key storage.** Keys live in environment variables (`.env`, mode 600). In production, prefer Docker or Kubernetes secrets or a KMS. **If the key is lost, the stored credentials cannot be recovered.**

## 5. Outbound request guard (SSRF)

Users supply hostnames, and devices report URIs, so every outbound connection is checked first (`services/netguard.py`):

- **Allowed schemes:** `http`, `https`, `rtsp`, `rtsps`, `whep`, `wheps`. `file:`, `gopher:` and similar are rejected.
- **Every resolved address is checked.** A hostname is resolved, and **all** of its addresses must pass. This stops DNS names that point at internal addresses.
- **Always blocked:** loopback, `0.0.0.0/8`, link-local `169.254/16` (which includes the cloud metadata address `169.254.169.254`), the AWS IPv6 metadata address, multicast and reserved ranges, IPv4-mapped forms of all of these, `*.internal`, and `*.localhost`.
- **Internal services are blocked.** Hostnames such as `postgres`, `mediamtx`, `backend`, `frontend`, `caddy` and `localhost` are refused, and their resolved IPs are refused too.
- **Private ranges** (RFC 1918, CGNAT, ULA) are allowed only when `ALLOW_PRIVATE_NETWORK_TARGETS=true`, which suits self-hosted LAN cameras. **Multi-tenant deployments must set it to `false`.**
- **Redirects** are followed by hand, at most 3 hops, and each hop is checked again. Credentials are dropped when a redirect goes to a different host.
- **RTSP probes** connect to the IP address that was already checked, not the hostname, so DNS cannot change in between.
- **Device-reported URIs are pinned.** ONVIF stream and snapshot URIs have their host replaced with the host the owner configured. A malicious or misconfigured device therefore cannot point the server anywhere else. This also fixes the common case where a camera behind NAT reports its LAN IP.
- **Size limits.** Images are capped at 6 MB, ONVIF responses at 512 KB and SDP at 64 KB. Image proxies check that the `Content-Type` is `image/*` or `multipart/*`.
- **Remaining risk.** HTTP requests made with httpx check the target before connecting. In theory, DNS rebinding could change the answer between that check and the connection. Running the backend behind an egress firewall that blocks the metadata service at the network level (see section 9) is the defence in depth.

## 6. Transport security

- **HTTPS everywhere.** Caddy terminates TLS 1.2 and 1.3, using Let's Encrypt or an internal CA. It sends HSTS (1 year) and redirects HTTP to HTTPS automatically.
- **WebRTC media** is encrypted with DTLS-SRTP. HLS segments travel over the same HTTPS origin.
- **Camera links.** RTSPS and HTTPS to cameras are supported and recommended for cameras reached over the internet. Most cameras use self-signed certificates. For RTSPS probes CameraScope accepts them, so the connection is encrypted but not authenticated. Prefer a VPN, such as WireGuard or Tailscale, to reach home cameras instead of forwarding ports.
- **Gateway exposure.** MediaMTX's API (9997), HLS (8888) and WHEP (8889) ports are **not published**. Its own access rules also accept only private-network callers. Only UDP 8189 (WebRTC media) is exposed, and it is useless without a WHEP session negotiated through the authenticated proxy.

## 7. Browser hardening

- **Content-Security-Policy:**
  - `default-src 'self'` and `object-src 'none'`
  - `frame-ancestors 'none'`
  - images only from self and the OSM tiles
  - `connect-src`/`media-src` limited to self and https (needed for public HLS CDNs)
- **Other headers:**
  - `X-Content-Type-Options: nosniff`
  - `Referrer-Policy: no-referrer`
  - `X-Frame-Options: DENY`
  - a `Permissions-Policy` that allows geolocation on this site only and blocks camera and microphone access
  - API responses carry `Cache-Control: no-store`
- **External links** use `rel="noopener noreferrer"`. Third-party pages are never framed.
- **Viewer privacy:**
  - Still images and MJPEG are proxied, so operators do not see viewers' IP addresses.
  - WebRTC uses no third-party STUN server.
  - Geolocation is requested only when the user clicks *Use my location*. It is used only for the search query and is not stored.

## 8. Auditing

The `audit_log` table records:

- registration, logins, failed logins and lockouts
- camera create, update and delete, including the authorisation basis
- ONVIF probes and refreshes
- views, with the quality chosen
- snapshots
- diagnostics
- admin provider syncs

Each entry has the user, target, IP and timestamp. Passwords and credentials are never logged.

## 9. Local Camera Gateways (v2)

**Trust model.**
- The cloud is trusted to run your dashboard.
- Each gateway is trusted by, and controlled by, the owner of its network.
- The tunnel is designed so that even a compromised cloud can reach only what each network owner allowed.

| Control | Where it is enforced |
|---|---|
| **Outbound only.** The agent dials `wss://<your domain>/api/gateways/tunnel` and never listens on the LAN, except for a MediaMTX auth hook on the private Docker network. No router port-forwarding. | Gateway |
| **Enrollment.** A one-time code, valid 30 minutes, is exchanged for a random 320-bit gateway secret. The cloud stores only its SHA-256 hash and compares in constant time. *Re-enroll* revokes the old secret and drops the live tunnel. | Cloud |
| **Transport.** TLS from Caddy (Let's Encrypt) with certificate verification in the agent. Optionally a private CA via `CLOUD_CA_FILE`. Environment proxies are not used. | Both |
| **Allow-list.** Every TCP connection and every stream source the cloud asks for is resolved **on the gateway** and must fall inside `ALLOWED_SUBNETS` and `ALLOWED_PORTS`. Anything else is refused and logged as `REFUSED`. This was tested: a camera registered outside the subnet was refused by the gateway. | Gateway |
| **No scanning.** The protocol has no sweep or discovery-by-range operation. The cloud can only open connections to hosts the owner explicitly registered. Reachability checks are plain TCP connects to those registered hosts, without credentials. | Gateway and cloud |
| **Gateway MediaMTX** publishes no ports. Reads require the relay account, which is checked by the agent's auth hook. Nobody may publish. Other LAN devices cannot pull feeds from it. | Gateway |
| **Cloud forwarders** (the cloud end of each tunnel) listen only on the internal Docker network and accept connections only from the cloud MediaMTX container. | Cloud |
| **Device-reported URLs are pinned.** ONVIF/NVR stream and snapshot URIs have their host replaced with the address the owner configured, then they are routed through that camera's gateway. A device cannot redirect the cloud anywhere else. | Cloud |
| **Credentials in transit.** Camera passwords are decrypted in the cloud only when needed and travel only inside the TLS tunnel to the gateway's MediaMTX (memory only), or in the RTSP/ONVIF/HTTP authentication to the camera. | Both |
| **No proximity assumptions.** Whether a camera is reached "via gateway X" or "Direct / VPN" is chosen explicitly. Status comes from measured reachability from that path, never from location. | Cloud |
| **Single backend worker.** Tunnels live in the backend process. Run exactly one backend replica. | Ops |

Remaining risk: the hop between the gateway (or the cloud, for Direct/VPN cameras) and the camera uses the camera's own protocol. RTSP and HTTP are unencrypted on the LAN unless the camera supports RTSPS or HTTPS, and cameras' self-signed certificates are not verified on that hop. Keep cameras on a dedicated VLAN and list only that VLAN in `ALLOWED_SUBNETS`.

## 10. Roles and sharing (v2)

- **Global roles:** `admin`, which is the first account and can trigger public-provider sync, and `user`.
- **Building (site) roles:**
  - **manager:** can add people and assign *their own* cameras or recorders to the building.
  - **viewer:** can only watch the building's cameras.
- **Shared viewers** receive a camera's name, status and playback only. The API never sends them its address, port, stream paths, username or last error. Diagnostics, editing and deletion are **owner-only** (404 for everyone else).
- Removing a member immediately revokes access, including through favorites, groups and the media proxy. Their own cameras leave the building at the same time.

## 11. Secrets never reach the browser (v2)

- Playback descriptors contain only the authenticated proxy paths `/api/media/<random-path>/…`. They never contain RTSP URLs, hosts or credentials.
- ONVIF probing returns profile names, tokens and resolutions only. The server resolves the stream URIs itself and stores them server-side. API responses strip `resolved_*` keys from the camera configuration.
- The browser tests scan every `/api/*` response and every console message for camera passwords, `rtsp://` and the relay secret. They found none.

## 12. Operator checklist

- [ ] Run `scripts/generate-secrets.sh`. Never reuse the example values. Back up `CREDENTIAL_KEYS` securely.
- [ ] Set `SITE_ADDRESS` to a real domain so a trusted certificate is issued.
- [ ] For a multi-user or public instance, set `ALLOW_PRIVATE_NETWORK_TARGETS=false`.
- [ ] At the host or cloud firewall, block traffic from the backend and gateway to the metadata service (`169.254.169.254`) and to admin networks.
- [ ] Expose only 80/tcp, 443/tcp, 443/udp and 8189/udp.
- [ ] Keep images up to date (`docker compose pull && docker compose up -d --build`). The database is on an `internal` network with no internet access.
- [ ] Review `audit_log` regularly for signs of misuse, such as many ONVIF probes or repeated `auth_failed` cameras.
- [ ] Publish an acceptable-use policy saying users may add only cameras they own or are authorised to access, and act on abuse reports.

## 13. Reporting vulnerabilities

Please report security issues privately to the maintainers rather than opening a public issue. Include reproduction steps. We aim to acknowledge reports within 72 hours.
