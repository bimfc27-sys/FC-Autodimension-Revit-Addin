# Deploying CameraScope

> **No server or domain?** See [FREE-DEPLOYMENT.md](FREE-DEPLOYMENT.md) to run CameraScope for free on your own Windows PC with Tailscale. This page covers deploying on a VPS with your own domain.

This guide puts CameraScope on a public HTTPS domain and connects your camera networks to it.

## 1. What you need

| Item | Why | Example |
|---|---|---|
| A Linux server with a public IPv4 address | Runs the cloud stack | Any VPS with 2 vCPU and 2–4 GB RAM (Hetzner, DigitalOcean, Lightsail, OVH…), Ubuntu 22.04/24.04 |
| A domain name, or a subdomain you control | HTTPS certificate and the URL you open on your phone | `cams.example.com` |
| A DNS **A record** pointing that name at the server | Let's Encrypt validation | `cams.example.com → 203.0.113.10` |
| Open firewall ports | Web and video | **80/tcp, 443/tcp, 443/udp** (HTTPS) and **8189/udp + 8189/tcp** (WebRTC video) |
| Docker 24+ with Compose v2 on the server | Runs everything | `curl -fsSL https://get.docker.com \| sh` |
| One always-on device per camera network | Local Camera Gateway | Raspberry Pi 4/5, NAS with Docker, mini-PC |

Bandwidth: each viewed stream passes through the server. A 1080p main stream uses about 2–6 Mbit/s and a sub stream about 0.3–1 Mbit/s. On phones, use the SD (sub stream) quality for 4- and 9-camera layouts.

## 2. Deploy the cloud stack

```bash
git clone <your repository> camerascope && cd camerascope/camerascope
./scripts/generate-secrets.sh            # writes .env (mode 600) with random secrets
nano .env
#   SITE_ADDRESS=cams.example.com        # your domain → automatic Let's Encrypt certificate
#   PUBLIC_HOST=203.0.113.10             # server public IP (WebRTC media address)
#   ALLOW_PRIVATE_NETWORK_TARGETS=false  # recommended when all cameras use gateways
docker compose up -d --build
docker compose ps                          # all services "running"/"healthy"
```

Then:

1. Open `https://cams.example.com` and **register immediately**. The first account becomes the administrator.
2. **Back up** `.env`, and the `pgdata` volume regularly. Without `CREDENTIAL_KEYS`, the stored camera passwords cannot be decrypted.
3. The backend applies database migrations automatically at startup. To run them by hand: `docker compose exec backend python -m app.migrate`.

**Updating:** `git pull && docker compose up -d --build`. Migrations run automatically.

## 3. Connect your cameras

CameraScope reaches each camera in one of two ways. You choose which one when you add the camera. Location is never used to decide this.

| Your situation | Connection | What to do |
|---|---|---|
| Cameras on your home or office Wi-Fi (the same network your phone uses at home) | **Gateway** | Install a gateway on that network (below) |
| Authorised cameras on a different network (another site, a relative's house, an office) | **Gateway**, one per network | Install a gateway on each network |
| Cameras behind a DVR/NVR | **Gateway** on the recorder's network | Add the recorder under *Manage → DVR / NVR* and discover its channels |
| Camera or NVR with a public address, or reachable through a VPN that the server is part of | **Direct / VPN** | Add the camera with "Direct / VPN" and its public or VPN address |

### Install a Local Camera Gateway (one per camera network)

1. In CameraScope go to **Manage → Gateways → Add gateway**. Copy the one-time enrollment code. It expires after 30 minutes.
2. On the always-on device in that network:
   ```bash
   # copy the camerascope/gateway folder to the device, then:
   cd gateway
   cp .env.example .env
   nano .env     # CLOUD_URL=https://cams.example.com
                 # ENROLLMENT_CODE=<code from step 1>
                 # ALLOWED_SUBNETS=192.168.1.0/24   (your camera LAN; several allowed, comma separated)
   ./generate-relay-secret.sh
   docker compose up -d --build
   docker compose logs -f agent    # "enrolled as gateway …", then "connected to https://…"
   ```
3. The gateway shows **Online** in CameraScope. Now add cameras or recorders and select this gateway. Use the cameras' addresses as seen from that network, for example `192.168.1.20`.

The gateway needs **no inbound ports and no router changes**. It keeps an outbound, TLS-encrypted WebSocket to your domain, reconnects by itself, and refuses any destination outside `ALLOWED_SUBNETS` / `ALLOWED_PORTS`.

### Using a VPN instead of a gateway (optional)

If the server is part of your WireGuard or Tailscale network, cameras on routed VPN subnets can be added as **Direct / VPN** using their VPN or LAN address. In that case, keep `ALLOW_PRIVATE_NETWORK_TARGETS=true` and make sure the `backend` and `mediamtx` containers can route to those subnets. The simplest way is to run the VPN client on the host and enable subnet routing. Gateways are usually simpler and are recommended.

### DVR / NVR

**Manage → DVR / NVR → Add** asks for the recorder's address on its network, its HTTP/ONVIF port, RTSP port and login, and the gateway it sits behind. CameraScope then discovers the channels:

- through **ONVIF** (most recorders), or
- through the **Hikvision ISAPI** or **Dahua / Amcrest** APIs.

Tick the channels you want and they become cameras that share the recorder's encrypted credentials. If discovery is not supported for your recorder, add each channel manually as an RTSP camera through the same gateway, using the recorder's documented RTSP path for each channel.

## 4. Using it on your phone

- Open `https://cams.example.com` in **iPhone Safari** or **Android Chrome**. Use **Share → Add to Home Screen** for an app-like icon.
- **Cameras** lists favorites, your groups, all your cameras grouped by the gateway they connect through, and **Building cameras** shared with you.
- **Live** shows 1, 4 or 9 feeds. Video uses WebRTC, with HLS as an automatic fallback. Tiles reconnect automatically after network changes, gateway restarts or when the app returns to the foreground.
- It works the same on home Wi-Fi, mobile data or any other network, because the phone always talks to your HTTPS domain and the gateway brings the video there. On restrictive mobile networks that block UDP, WebRTC uses TCP 8189, or playback falls back to HLS over 443.

## 5. Operations checklist

- [ ] `.env` secrets generated by the script, file mode 600, backed up offline.
- [ ] Only ports 80, 443 and 8189 open on the server firewall. Block outbound access to `169.254.169.254` at the provider firewall.
- [ ] `ALLOW_PRIVATE_NETWORK_TARGETS=false` if every camera uses a gateway.
- [ ] Each gateway's `ALLOWED_SUBNETS` lists only that site's camera VLAN or subnet.
- [ ] Back up the `pgdata` volume, for example nightly `docker compose exec postgres pg_dump -U camerascope camerascope | gzip > backup.sql.gz`.
- [ ] Review **audit_log** periodically: logins, gateway connects and disconnects, camera views, credential changes.
- [ ] Update regularly (`git pull && docker compose up -d --build`).

## 6. Troubleshooting

| Symptom | Check |
|---|---|
| Gateway stays "Waiting for install" | Agent logs. Is `CLOUD_URL` https:// with your exact domain? Was the code used within 30 minutes? If needed, use *Re-enroll* to get a new code. |
| Gateway "Offline" | The device is powered and has internet. `docker compose ps` on the gateway. The agent reconnects with backoff, so no action is needed once the network is back. |
| Camera "Gateway offline" | The camera's gateway is offline (see above). |
| Camera "Offline" | Run **Diagnostics**. "Gateway refused … outside ALLOWED_SUBNETS" means you should add the subnet on the gateway. "TCP unreachable" means a wrong IP or port, or the camera is off. |
| Camera "Login rejected" | Wrong username or password. After 3 rejections CameraScope locks the camera and never retries on its own. Edit the credentials to unlock it. |
| Video black on iPhone and Android over mobile data | Ports 8189 udp/tcp are not open, or `PUBLIC_HOST` is wrong. HLS fallback should still play after about 12 s. |
| H.265 camera shows no picture | Use the camera's H.264 sub stream, or set the main stream to H.264. Browser support for H.265 varies. |
