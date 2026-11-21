# Free deployment: your Windows PC + Tailscale

No VPS, no domain, no extra hardware, no paid subscription. CameraScope runs on a Windows PC you already have, and you open it on your iPhone through a free HTTPS URL.

## 1. What is and is not possible for free

| You want | Free solution | Works? | Limits |
|---|---|---|---|
| A web URL on your iPhone | CameraScope runs on your **Windows PC** (Docker Desktop). **Tailscale Serve** publishes it as `https://<pc-name>.<tailnet>.ts.net` with a valid certificate. You install the free **Tailscale app** on the iPhone. | ✅ | The PC must be **on and awake**. Remote viewing speed is limited by your home internet **upload** speed. |
| A URL that works without the Tailscale app | **Tailscale Funnel** publishes the same URL to the public internet. *Alternative:* a Cloudflare Quick Tunnel (no account needed). | ✅ but video uses **HLS** (2–5 s delay) | WebRTC video cannot pass through these HTTPS-only tunnels. Funnel has unpublished bandwidth limits. The Cloudflare Quick Tunnel URL **changes on every start**, and Cloudflare says Quick Tunnels are for testing only. |
| Login and password protection | Built-in accounts: argon2 password hashes, lockout after failed attempts, HTTPS-only cookies, audit log. Self-registration closes after the first account (you become the administrator), and you create other people's accounts yourself. | ✅ | With Funnel the login page is on the public internet: use a long, unique password. |
| Add your own IP cameras by IP, username and password | The PC connects to cameras on **its own network** directly (RTSP, ONVIF, HLS, WebRTC, MJPEG, Axis, Hikvision, Dahua, Amcrest, Reolink). | ✅ | The PC and the cameras must be on the same LAN, or the cameras must be routable from it. |
| Phone on the same Wi-Fi as the cameras | The phone uses the same URL. With the Tailscale app, traffic stays on your LAN (direct connection). | ✅ | — |
| DVR / NVR | ONVIF, Hikvision ISAPI or Dahua/Amcrest channel discovery, or manual channels. | ✅ on reachable networks | Real recorders vary. Run Diagnostics after adding one. |
| Authorised cameras on **other** Wi-Fi networks | **(a)** Install the CameraScope gateway on an **existing Windows PC at that site** (`gateway/windows`, no Docker needed). **(b)** If that site's router has a **built-in VPN server** (WireGuard/OpenVPN), connect the home PC to it and add the cameras as "Direct / VPN". **(c)** Router port-forwarding to the camera works, but exposes it to the internet. Not recommended. | ✅ with (a), (b) or (c) | — |
| A remote network with **no** computer, **no** router VPN and **no** port-forward | — | ❌ **Not possible.** | No free or paid tunnel can reach a network that has nothing inside it to connect out, and being nearby doesn't change that. CameraScope will show the camera as offline or unreachable; it will never pretend it is online. |
| Manufacturer P2P clouds (Hik-Connect, DMSS, Reolink UID…) | — | ❌ Not supported | Proprietary, undocumented protocols. |
| 24/7 availability while the PC is off or asleep | — | ❌ Not for free | Free cloud tiers don't fit: no UDP for video, apps that sleep when idle, databases that expire, or a credit card required. Keep the PC awake (see below). |

Licences and plans, checked November 2026; they may change:

- **Docker Desktop** is free for personal use and for small businesses (fewer than 250 employees *and* under $10M revenue).
- **Tailscale**'s Personal plan is free, and **Funnel** is available on all plans, on ports 443, 8443 and 10000 only.
- **Cloudflare Quick Tunnels** need no account and have a limit of 200 concurrent requests.

## 2. Architecture

```
 iPhone (Safari)                                         Windows PC at home (always on)
  ├─ with Tailscale app ── WireGuard (direct/relayed) ──► tailscaled ─► 127.0.0.1:8080 ─► CameraScope (Docker Desktop)
  │      HTTPS + WebRTC video (UDP/TCP 8189, tailnet only)                    │  web, API, DB, MediaMTX
  └─ without app ── HTTPS ──► Tailscale Funnel ──────────► (same)            ├─► cameras / NVR on the home LAN (direct)
         HLS video only                                                       └─◄ outbound wss tunnel ── gateway on an
                                                                                    EXISTING PC at another site ─► its cameras / NVR
```

- Nothing listens on your LAN or opens router ports. CameraScope's entry point is `127.0.0.1:8080`. The WebRTC port 8189 is allowed by Windows Firewall **only from your tailnet** (100.64.0.0/10).
- A remote-site gateway connects **out** to your PC's `https://…ts.net` URL. It does that either through the tailnet, if Tailscale is installed on that PC (recommended), or through Funnel.

## 3. Install on the home PC (about 20 minutes)

Requirements: Windows 10/11 64-bit with virtualization enabled, and about 4 GB of free RAM.

1. **Install Docker Desktop** (free for personal use): `winget install -e --id Docker.DockerDesktop`. Start it once and enable *Start Docker Desktop when you sign in*.
2. **Install Tailscale:** `winget install -e --id Tailscale.Tailscale`, then sign in from the tray icon. Create a free account with Google, Microsoft, Apple or GitHub.
3. In the Tailscale **admin console → DNS**, enable **MagicDNS** and **HTTPS Certificates**. If you skip this, `install.ps1` prints a link that enables them.
4. Download CameraScope from your GitHub repository: *Code → Download ZIP*, then unzip it.
5. Open **PowerShell as Administrator** in `camerascope\deploy\windows` and run:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\install.ps1           # private: tailnet only (recommended)
   # or
   powershell -ExecutionPolicy Bypass -File .\install.ps1 -Public   # also public via Tailscale Funnel
   ```
   The script:
   - generates secrets into `.env`, readable only by you (back it up)
   - builds and starts the stack
   - adds the firewall rule limited to the tailnet
   - publishes the URL and prints it
6. Keep the PC awake while plugged in: `powercfg /change standby-timeout-ac 0`. Windows Update restarts are fine: Docker Desktop restarts CameraScope after you sign in.

## 4. Use it on the iPhone

- **Private mode (recommended):** install **Tailscale** from the App Store and sign in with the same account. Then open `https://<pc-name>.<tailnet>.ts.net` in Safari and **register**: the first account is the administrator. Use *Share → Add to Home Screen* for an app icon. Video uses **WebRTC** (lowest delay) and falls back to HLS automatically.
- **Public mode (no app):** open the same URL from any browser. In CameraScope go to **Manage → Settings → Video transport** and choose **"HLS only"** on that phone. If you leave it on Automatic, CameraScope detects that WebRTC is blocked and switches to HLS on its own, but the first picture takes about 12 s instead of about 2–4 s.
- **Accounts for other people:** **Manage → Settings → Accounts**. Share cameras with them through **Manage → Buildings**.

## 5. Add cameras

- **Cameras on the home network:** **Cameras → + Add** → "Direct / VPN". Enter the camera's LAN IP (for example `192.168.1.20`), the port, the stream path and the username/password, then confirm you're authorised. The PC connects to it directly.
- **NVR / DVR:** **Manage → DVR / NVR → Add**, then *Discover channels* and pick the channels you want.
- **Another site:** follow the steps below.

### A camera network at another site (existing Windows PC there)

1. In CameraScope, go to **Manage → Gateways → Add gateway** and copy the one-time code (valid 30 minutes).
2. On the PC at that site:
   - Install **Tailscale** and sign in to *your* tailnet (recommended), so it can reach your home URL privately. If you skip this, enable Funnel at home with `install.ps1 -Public`.
   - Install Python as an all-users install: `winget install -e --id Python.Python.3.12 --scope machine`.
3. Copy the `camerascope\gateway` folder to that PC, open **PowerShell as Administrator** in `gateway\windows`, and run:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\install-gateway.ps1 `
     -CloudUrl https://<home-pc>.<tailnet>.ts.net `
     -EnrollmentCode <code> `
     -AllowedSubnets 192.168.0.0/24
   ```
   The script:
   - downloads the official MediaMTX build and checks its SHA-256 against the release checksums
   - creates a startup task that runs as SYSTEM and restarts the gateway on failure
   - waits until the gateway is **Online**

   The gateway only connects **out**, and refuses any address outside `-AllowedSubnets`.
4. Add that site's cameras or NVR in CameraScope and choose this gateway under **Reached through**.

If the site has no PC but its router has a VPN server, connect the home PC to that VPN instead and add the cameras as "Direct / VPN" with their LAN addresses. Docker Desktop containers use the Windows host's network routes, but **check this with Diagnostics**: this path was not tested here.

## 6. Everyday operation

| Task | Command (in `deploy\windows`) |
|---|---|
| Status, health and published URL | `.\status.ps1` |
| Stop, keeping your data | `.\stop.ps1` |
| Start again (add `-Public` for Funnel) | `.\start.ps1` |
| Update after downloading a new version | `.\update.ps1`. Database migrations run automatically. |
| Back up the database | `.\backup.ps1`. Keep it together with a copy of `.env`. |
| Temporary public URL without Tailscale | `.\cloudflare-quick-tunnel.ps1`. The URL changes every run. |

## 7. Security notes for this setup

- `REGISTRATION_MODE=first_user_only`: only the first account can self-register. Everyone else gets HTTP 403 and the sign-up link is hidden.
- `ALLOW_PRIVATE_NETWORK_TARGETS=true` is required so the PC can reach LAN cameras. Because of this, **only give accounts to people you trust**: a logged-in user could point a "camera" at another device on your LAN. Infrastructure hostnames and the cloud metadata service stay blocked.
- In private mode, nothing is reachable from the internet at all; Tailscale authenticates every device. In public mode, everything sits behind CameraScope's login: lockout after 5 failed attempts, plus per-IP and per-e-mail rate limits.
- Camera passwords are stored encrypted (AES-256-GCM) and never sent to the browser.

## 8. What was verified, and what was not

Verified in the test lab (Linux, Docker, Chromium emulating an iPhone 13). Here, an HTTPS reverse proxy stood in for Tailscale Serve, because Tailscale and Windows were not available:

- ✅ The unmodified `deploy/windows/docker-compose.home.yml` stack:
  - comes up with migrations
  - serves `127.0.0.1:8080` only
  - works behind the HTTPS proxy
- ✅ First account → admin. A stranger's self-registration → **403**, and the login page hides sign-up. The admin creates a family account from the phone UI.
- ✅ A LAN camera added by IP, username and password plays over **WebRTC** (about 4.5 s to first picture).
- ✅ Public-tunnel conditions, simulated by blocking port 8189 completely:
  - *Automatic* falls back to **HLS** and plays (about 12.6 s).
  - *HLS only* plays immediately.
- ✅ A camera on a **separate, isolated network** plays via a gateway that connects *through the HTTPS entry point* into the home stack. Both cameras played together in the phone layout.
- ✅ A new camera's status shows *Online* within about 2 s of adding it. The gateway reconnects automatically after the home server restarts.

**Not verified:**

- The **PowerShell scripts** (`install.ps1`, `install-gateway.ps1`, …) were reviewed and statically checked but **never executed**. No Windows machine or PowerShell was available, and downloading PowerShell was blocked by the test environment's policy. Expect possible first-run fixes; report any error text and it can be corrected quickly.
- **Tailscale Serve/Funnel and Cloudflare Quick Tunnels** themselves. They were not reachable from the test environment.
- **Docker Desktop on Windows**: networking from containers to your LAN, and the firewall rule.
- **Real iPhone Safari** and your **real cameras or NVRs**.
- That MediaMTX's GitHub release publishes `checksums.sha256` in the expected format. If it doesn't, the gateway installer stops rather than install an unverified binary.
