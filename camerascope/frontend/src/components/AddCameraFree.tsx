"use client";

import { FormEvent, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { DiagnosticStep, PrivateCamera, Site } from "@/lib/types";

export interface BrandInfo {
  id: string;
  label: string;
  kinds: ("camera" | "nvr")[];
  default_http_port: number;
  video: "snapshot" | "none";
  has_default_snapshot: boolean;
  channel_discovery: "hikvision" | "dahua" | null;
  notes: string;
}

interface Props {
  sites: Site[];
  onClose: () => void;
  onCreated: (cam: PrivateCamera) => void;
}

export function Steps({ steps }: { steps: DiagnosticStep[] }) {
  return (
    <div className="steps">
      {steps.map((s, i) => (
        <div className="step" key={i}>
          <span className={`ic ${s.ok ? "ok" : "bad"}`}>{s.ok ? "✔" : "✖"}</span>
          <div>
            <div style={{ fontWeight: 600 }}>{s.name}</div>
            <div className="small muted" style={{ wordBreak: "break-word" }}>{s.detail}</div>
          </div>
          <span className="small muted">{s.duration_ms ? `${s.duration_ms} ms` : ""}</span>
        </div>
      ))}
    </div>
  );
}

export default function AddCameraFree({ sites, onClose, onCreated }: Props) {
  const [brands, setBrands] = useState<BrandInfo[]>([]);
  const [brandId, setBrandId] = useState("hikvision");
  const [f, setF] = useState({ name: "", host: "", port: "", https: false, channel: "1", snapshot_path: "", refresh: "3", username: "", password: "", model: "", serial: "", notes: "", site_id: "", basis: "owner" });
  const [attested, setAttested] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<(PrivateCamera & { verification: { ok: boolean; steps: DiagnosticStep[] } }) | null>(null);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });

  useEffect(() => {
    api<BrandInfo[]>("/api/cameras/brands").then(setBrands).catch((e) => setError((e as Error).message));
  }, []);
  const b = brands.find((x) => x.id === brandId);
  const needsPath = b && b.video === "snapshot" && !b.has_default_snapshot;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!attested) return setError("You must confirm that you own or are authorised to access this camera.");
    setBusy(true);
    setError(null);
    try {
      const cam = await api<PrivateCamera & { verification: { ok: boolean; steps: DiagnosticStep[] } }>("/api/cameras", {
        method: "POST",
        json: {
          name: f.name, brand: brandId, host: f.host, port: f.port ? Number(f.port) : (f.https ? 443 : b?.default_http_port ?? 80), use_tls: f.https,
          channel: Number(f.channel || 1), snapshot_path: f.snapshot_path || null, refresh_seconds: Number(f.refresh || 3),
          credentials: f.username || f.password ? { username: f.username, password: f.password } : null,
          model: f.model || null, serial: f.serial || null, notes: f.notes || null, site_id: f.site_id || null,
          authorization_basis: f.basis, authorization_attested: true,
        },
      });
      setResult(cam);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (result) {
    const ok = result.video?.available;
    return (
      <div className="modal-back">
        <div className="modal">
          <h3 style={{ margin: 0 }}>{ok ? "Camera added – snapshot view enabled" : "Camera saved – no video yet"}</h3>
          <div className={ok ? "notice small" : "error"}>
            {ok
              ? `Verified: a real image was received from ${result.name}. It refreshes every ${result.video?.refresh_seconds ?? 3} s. This is not live video.`
              : result.video?.reason ?? "Verification did not succeed."}
          </div>
          <Steps steps={result.verification.steps} />
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button className="btn primary" onClick={() => onCreated(result)}>{ok ? "Open camera" : "Done"}</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <form className="modal" onSubmit={submit} autoComplete="off">
        <div className="row">
          <h3 className="grow" style={{ margin: 0 }}>Add a camera</h3>
          <button type="button" className="btn icon sm" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="notice small">
          Free Edition: CameraScope runs on Cloudflare, so it can only reach cameras that are reachable <strong>from the internet</strong>
          (your router&apos;s public IP or a DDNS name + port-forward). It shows a <strong>snapshot view</strong> refreshed every few seconds –
          live RTSP video is not possible on Cloudflare&apos;s free services. Passwords are encrypted and never sent to this browser.
        </div>

        <label className="field">Brand
          <select className="select" value={brandId} onChange={(e) => setBrandId(e.target.value)}>
            {brands.map((x) => <option key={x.id} value={x.id}>{x.label}{x.video === "none" ? " – no video in Free Edition" : ""}</option>)}
          </select>
        </label>
        {b && <div className={b.video === "none" ? "error small" : "small muted"}>{b.notes}</div>}

        <label className="field">Name<input className="input" value={f.name} onChange={set("name")} required maxLength={200} placeholder="Front door" /></label>
        <div className="grid2">
          <label className="field">Public IP or DDNS name
            <input className="input" value={f.host} onChange={(e) => setF({ ...f, host: e.target.value.trim() })} required placeholder="myhome.ddns.net" autoCapitalize="none" autoCorrect="off" />
          </label>
          <label className="field">Forwarded HTTP(S) port
            <input className="input" type="number" inputMode="numeric" value={f.port} onChange={set("port")} placeholder={String(f.https ? 443 : b?.default_http_port ?? 80)} />
          </label>
        </div>
        <label className="row small"><input type="checkbox" checked={f.https} onChange={(e) => setF({ ...f, https: e.target.checked })} /> HTTPS (recommended; the camera needs a certificate trusted by Cloudflare)</label>
        <div className="grid2">
          <label className="field">Channel<input className="input" type="number" inputMode="numeric" min={1} max={256} value={f.channel} onChange={set("channel")} /></label>
          <label className="field">Refresh every (seconds)<input className="input" type="number" inputMode="numeric" min={2} max={60} value={f.refresh} onChange={set("refresh")} /></label>
        </div>
        {b?.video === "snapshot" && (
          <label className="field">Snapshot path {needsPath ? "(required for this brand)" : "(optional – leave empty for the documented default)"}
            <input className="input" value={f.snapshot_path} onChange={set("snapshot_path")} required={!!needsPath} placeholder="/path/to/snapshot.jpg" autoCapitalize="none" />
          </label>
        )}
        <div className="grid2">
          <label className="field">Camera username<input className="input" value={f.username} onChange={set("username")} autoCapitalize="none" autoCorrect="off" /></label>
          <label className="field">Camera password<input className="input" type="password" value={f.password} onChange={set("password")} autoComplete="new-password" /></label>
        </div>
        <details>
          <summary className="small">Device details (optional)</summary>
          <div className="grid2" style={{ marginTop: 8 }}>
            <label className="field">Model<input className="input" value={f.model} onChange={set("model")} /></label>
            <label className="field">Serial number<input className="input" value={f.serial} onChange={set("serial")} /></label>
          </div>
          <label className="field">Notes<input className="input" value={f.notes} onChange={set("notes")} /></label>
        </details>
        {sites.some((s) => s.role === "manager") && (
          <label className="field">Building (optional)
            <select className="select" value={f.site_id} onChange={set("site_id")}>
              <option value="">Not shared</option>
              {sites.filter((s) => s.role === "manager").map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
        )}
        <div className="attest">
          <input id="attest-free" type="checkbox" checked={attested} onChange={(e) => setAttested(e.target.checked)} />
          <label htmlFor="attest-free" className="small">
            I confirm that I{" "}
            <select className="select mini" value={f.basis} onChange={set("basis")}>
              <option value="owner">own this camera</option>
              <option value="authorized_user">have explicit authorisation from its owner</option>
            </select>{" "}
            and that these credentials were issued to me. CameraScope tries them once and never guesses passwords.
          </label>
        </div>
        {error && <div className="error">{error}</div>}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={busy || !attested}>{busy ? "Saving and testing…" : "Save and test"}</button>
        </div>
      </form>
    </div>
  );
}
