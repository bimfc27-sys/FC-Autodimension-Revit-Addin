"use client";

import { FormEvent, useState } from "react";
import { api } from "@/lib/api";
import type { CameraConfig, OnvifProfile, PrivateCamera, Protocol, Vendor } from "@/lib/types";

interface Props {
  onClose: () => void;
  onCreated: (cam: PrivateCamera) => void;
  defaultLocation: [number, number] | null;
}

const PROTOCOLS: { id: Protocol; label: string; hint: string }[] = [
  { id: "rtsp", label: "RTSP", hint: "rtsp://host:554/path – relayed to the browser as WebRTC/HLS" },
  { id: "onvif", label: "ONVIF", hint: "Profiles and stream URIs are read from the device" },
  { id: "vendor_api", label: "Manufacturer API", hint: "Documented endpoints for Axis, Hikvision, Dahua, Amcrest, Reolink" },
  { id: "webrtc", label: "WebRTC (WHEP)", hint: "Camera or NVR exposing a WHEP endpoint" },
  { id: "hls", label: "HLS", hint: "Camera or NVR publishing an .m3u8 playlist" },
  { id: "mjpeg", label: "MJPEG / HTTP", hint: "Multipart JPEG stream over HTTP(S)" },
];

const DEFAULT_PORT: Record<Protocol, number> = { rtsp: 554, onvif: 80, vendor_api: 554, webrtc: 8889, hls: 80, mjpeg: 80 };

export default function AddCameraDialog({ onClose, onCreated, defaultLocation }: Props) {
  const [protocol, setProtocol] = useState<Protocol>("rtsp");
  const [vendor, setVendor] = useState<Vendor>("generic");
  const [name, setName] = useState("");
  const [host, setHost] = useState("");
  const [port, setPort] = useState<string>("");
  const [useTls, setUseTls] = useState(false);
  const [mainPath, setMainPath] = useState("");
  const [subPath, setSubPath] = useState("");
  const [snapshotPath, setSnapshotPath] = useState("");
  const [channel, setChannel] = useState(1);
  const [transport, setTransport] = useState<"tcp" | "udp" | "automatic">("tcp");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [snapshotAllowed, setSnapshotAllowed] = useState(true);
  const [basis, setBasis] = useState<"owner" | "authorized_user">("owner");
  const [attested, setAttested] = useState(false);
  const [lat, setLat] = useState(defaultLocation ? defaultLocation[0].toFixed(5) : "");
  const [lon, setLon] = useState(defaultLocation ? defaultLocation[1].toFixed(5) : "");
  const [profiles, setProfiles] = useState<OnvifProfile[] | null>(null);
  const [device, setDevice] = useState<string | null>(null);
  const [mainProfile, setMainProfile] = useState("");
  const [subProfile, setSubProfile] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const effectivePort = port ? Number(port) : DEFAULT_PORT[protocol];

  async function probeOnvif() {
    setError(null);
    if (!attested) return setError("Confirm your authorisation before connecting to the device.");
    setBusy(true);
    try {
      const res = await api<{ manufacturer: string; model: string; profiles: OnvifProfile[] }>("/api/cameras/onvif/probe", {
        method: "POST",
        json: { host, port: effectivePort, use_tls: useTls, credentials: { username, password }, authorization_attested: attested },
      });
      setProfiles(res.profiles);
      setDevice(`${res.manufacturer ?? ""} ${res.model ?? ""}`.trim());
      setMainProfile(res.profiles[0]?.token ?? "");
      setSubProfile(res.profiles[1]?.token ?? "");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!attested) return setError("You must confirm that you own or are authorised to access this camera.");
    const config: CameraConfig = {
      main_path: mainPath || null,
      sub_path: subPath || null,
      snapshot_path: snapshotPath || null,
      rtsp_transport: transport,
      channel,
      snapshot_allowed: snapshotAllowed,
    };
    if (protocol === "onvif") {
      if (!profiles) return setError("Query the device first to select its stream profiles.");
      const m = profiles.find((p) => p.token === mainProfile);
      const s = profiles.find((p) => p.token === subProfile);
      Object.assign(config, {
        onvif_main_profile: m?.token ?? null,
        onvif_sub_profile: s && s.token !== m?.token ? s.token : null,
        resolved_main_uri: m?.stream_uri ?? null,
        resolved_sub_uri: s && s.token !== m?.token ? s.stream_uri : null,
        resolved_snapshot_uri: m?.snapshot_uri ?? null,
      });
    }
    setBusy(true);
    try {
      const cam = await api<PrivateCamera>("/api/cameras", {
        method: "POST",
        json: {
          name,
          protocol,
          vendor: protocol === "vendor_api" ? vendor : "generic",
          host,
          port: effectivePort,
          use_tls: useTls,
          config,
          credentials: username || password ? { username, password } : null,
          latitude: lat ? Number(lat) : null,
          longitude: lon ? Number(lon) : null,
          authorization_basis: basis,
          authorization_attested: attested,
        },
      });
      onCreated(cam);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const needsPaths = protocol === "rtsp" || protocol === "hls" || protocol === "webrtc" || protocol === "mjpeg";

  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <form className="modal" onSubmit={submit} autoComplete="off">
        <div className="row">
          <h3 className="grow" style={{ margin: 0 }}>Add a private camera</h3>
          <span className="badge private">Private</span>
        </div>
        <div className="notice small">
          Only add cameras you own or have explicit permission to access. Credentials are encrypted (AES-256-GCM) and used only to
          connect to this device. CameraScope never guesses passwords or bypasses camera authentication.
        </div>

        <label className="field">
          Name
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} required maxLength={200} placeholder="Front door" />
        </label>

        <label className="field">
          Connection type
          <select className="select" value={protocol} onChange={(e) => { setProtocol(e.target.value as Protocol); setProfiles(null); }}>
            {PROTOCOLS.map((p) => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
          <span className="small">{PROTOCOLS.find((p) => p.id === protocol)?.hint}</span>
        </label>

        {protocol === "vendor_api" && (
          <div className="grid2">
            <label className="field">
              Manufacturer
              <select className="select" value={vendor} onChange={(e) => setVendor(e.target.value as Vendor)}>
                <option value="generic" disabled>Select…</option>
                <option value="axis">Axis (VAPIX)</option>
                <option value="hikvision">Hikvision (ISAPI)</option>
                <option value="dahua">Dahua</option>
                <option value="amcrest">Amcrest</option>
                <option value="reolink">Reolink</option>
              </select>
            </label>
            <label className="field">
              Channel
              <input className="input" type="number" min={1} max={64} value={channel} onChange={(e) => setChannel(Number(e.target.value))} />
            </label>
          </div>
        )}

        <div className="grid2">
          <label className="field">
            Host (IP or DNS name)
            <input className="input" value={host} onChange={(e) => setHost(e.target.value.trim())} required placeholder="cam.example.com or 203.0.113.7" />
          </label>
          <label className="field">
            Port
            <input className="input" type="number" min={1} max={65535} value={port} onChange={(e) => setPort(e.target.value)} placeholder={String(DEFAULT_PORT[protocol])} />
          </label>
        </div>
        <label className="row small">
          <input type="checkbox" checked={useTls} onChange={(e) => setUseTls(e.target.checked)} />
          Use TLS ({protocol === "rtsp" || protocol === "vendor_api" ? "RTSPS" : "HTTPS"}) – recommended for cameras reached over the internet
        </label>

        {needsPaths && (
          <div className="grid2">
            <label className="field">
              {protocol === "mjpeg" ? "Stream path" : "Main stream path"}
              <input className="input" value={mainPath} onChange={(e) => setMainPath(e.target.value)} required placeholder={protocol === "hls" ? "/live/index.m3u8" : protocol === "webrtc" ? "/cam/whep" : "/stream1"} />
            </label>
            {protocol !== "mjpeg" && (
              <label className="field">
                Sub stream path (low quality, optional)
                <input className="input" value={subPath} onChange={(e) => setSubPath(e.target.value)} placeholder="/stream2" />
              </label>
            )}
          </div>
        )}
        {(protocol === "rtsp" || protocol === "vendor_api") && (
          <div className="grid2">
            <label className="field">
              RTSP transport
              <select className="select" value={transport} onChange={(e) => setTransport(e.target.value as "tcp")}>
                <option value="tcp">TCP (recommended over internet)</option>
                <option value="udp">UDP</option>
                <option value="automatic">Automatic</option>
              </select>
            </label>
            <label className="field">
              Snapshot path (optional, HTTP)
              <input className="input" value={snapshotPath} onChange={(e) => setSnapshotPath(e.target.value)} placeholder="/snapshot.jpg" />
            </label>
          </div>
        )}

        <div className="grid2">
          <label className="field">
            Camera username
            <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="off" />
          </label>
          <label className="field">
            Camera password
            <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
          </label>
        </div>

        <div className="attest">
          <input id="attest" type="checkbox" checked={attested} onChange={(e) => setAttested(e.target.checked)} style={{ marginTop: 3 }} />
          <label htmlFor="attest" className="small">
            I confirm that I{" "}
            <select className="select" style={{ display: "inline", width: "auto", padding: "1px 4px" }} value={basis} onChange={(e) => setBasis(e.target.value as "owner")}>
              <option value="owner">own this camera</option>
              <option value="authorized_user">have explicit authorisation from its owner</option>
            </select>{" "}
            and that the credentials above were issued to me. This attestation is recorded with a timestamp.
          </label>
        </div>

        {protocol === "onvif" && (
          <div className="cam-item" style={{ cursor: "default" }}>
            <div className="row">
              <strong className="grow">ONVIF device</strong>
              <button type="button" className="btn sm" onClick={probeOnvif} disabled={busy || !host}>Query device</button>
            </div>
            {device && <div className="small muted">Found: {device}</div>}
            {profiles && (
              <div className="grid2">
                <label className="field">
                  High quality profile
                  <select className="select" value={mainProfile} onChange={(e) => setMainProfile(e.target.value)}>
                    {profiles.map((p) => (
                      <option key={p.token} value={p.token}>{p.name} {p.width ? `${p.width}×${p.height}` : ""} {p.encoding ?? ""}</option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  Low quality profile
                  <select className="select" value={subProfile} onChange={(e) => setSubProfile(e.target.value)}>
                    <option value="">None</option>
                    {profiles.map((p) => (
                      <option key={p.token} value={p.token}>{p.name} {p.width ? `${p.width}×${p.height}` : ""}</option>
                    ))}
                  </select>
                </label>
              </div>
            )}
          </div>
        )}

        <div className="grid2">
          <label className="field">
            Latitude (optional, for the map)
            <input className="input" value={lat} onChange={(e) => setLat(e.target.value)} inputMode="decimal" />
          </label>
          <label className="field">
            Longitude
            <input className="input" value={lon} onChange={(e) => setLon(e.target.value)} inputMode="decimal" />
          </label>
        </div>
        <label className="row small">
          <input type="checkbox" checked={snapshotAllowed} onChange={(e) => setSnapshotAllowed(e.target.checked)} />
          Allow snapshots of this camera
        </label>

        {error && <div className="error">{error}</div>}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={busy || !attested}>{busy ? "Saving…" : "Add camera"}</button>
        </div>
      </form>
    </div>
  );
}
