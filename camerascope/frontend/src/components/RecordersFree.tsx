"use client";

import { FormEvent, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Device, DeviceChannel, PrivateCamera, Site } from "@/lib/types";
import type { BrandInfo } from "./AddCameraFree";
import { STATUS_TEXT } from "./CamerasPanel";

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-label={title}>
        <div className="row"><h3 className="grow" style={{ margin: 0 }}>{title}</h3><button className="btn icon sm" onClick={onClose} aria-label="Close">✕</button></div>
        {children}
      </div>
    </div>
  );
}

function AddRecorder({ sites, onClose, onCreated }: { sites: Site[]; onClose: () => void; onCreated: (d: Device) => void }) {
  const [brands, setBrands] = useState<BrandInfo[]>([]);
  const [f, setF] = useState({ name: "", brand: "hikvision", host: "", port: "", https: false, username: "", password: "", model: "", site_id: "", basis: "owner" });
  const [attested, setAttested] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  useEffect(() => { api<BrandInfo[]>("/api/cameras/brands").then((b) => setBrands(b.filter((x) => x.kinds.includes("nvr")))).catch(() => {}); }, []);
  const b = brands.find((x) => x.id === f.brand);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onCreated(await api<Device>("/api/devices", { method: "POST", json: {
        name: f.name, brand: f.brand, host: f.host, port: f.port ? Number(f.port) : (f.https ? 443 : b?.default_http_port ?? 80), use_tls: f.https,
        credentials: { username: f.username, password: f.password }, model: f.model || null, site_id: f.site_id || null,
        authorization_basis: f.basis, authorization_attested: attested,
      } }));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Add DVR / NVR" onClose={onClose}>
      <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 10 }} autoComplete="off">
        <div className="notice small">The recorder must be reachable from the internet (public IP/DDNS + port-forward of its HTTP/HTTPS port). Each channel gets a snapshot view after it is verified.</div>
        <label className="field">Brand
          <select className="select" value={f.brand} onChange={set("brand")}>
            {brands.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
          </select>
        </label>
        {b && <div className="small muted">{b.channel_discovery ? "Channels can be discovered automatically (documented vendor API)." : "Add channels manually after saving."} {b.notes}</div>}
        <label className="field">Name<input className="input" value={f.name} onChange={set("name")} required maxLength={200} placeholder="Shop NVR" /></label>
        <div className="grid2">
          <label className="field">Public IP or DDNS name<input className="input" value={f.host} onChange={(e) => setF({ ...f, host: e.target.value.trim() })} required autoCapitalize="none" autoCorrect="off" /></label>
          <label className="field">Forwarded HTTP(S) port<input className="input" type="number" inputMode="numeric" value={f.port} onChange={set("port")} placeholder={String(f.https ? 443 : b?.default_http_port ?? 80)} /></label>
        </div>
        <label className="row small"><input type="checkbox" checked={f.https} onChange={(e) => setF({ ...f, https: e.target.checked })} /> HTTPS</label>
        <div className="grid2">
          <label className="field">Recorder username<input className="input" value={f.username} onChange={set("username")} autoCapitalize="none" autoCorrect="off" /></label>
          <label className="field">Recorder password<input className="input" type="password" value={f.password} onChange={set("password")} autoComplete="new-password" /></label>
        </div>
        <label className="field">Model (optional)<input className="input" value={f.model} onChange={set("model")} /></label>
        {sites.some((s) => s.role === "manager") && (
          <label className="field">Building (optional)
            <select className="select" value={f.site_id} onChange={set("site_id")}>
              <option value="">Not shared</option>
              {sites.filter((s) => s.role === "manager").map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
        )}
        <div className="attest">
          <input id="attest-rec" type="checkbox" checked={attested} onChange={(e) => setAttested(e.target.checked)} />
          <label htmlFor="attest-rec" className="small">I confirm I <select className="select mini" value={f.basis} onChange={set("basis")}><option value="owner">own this recorder</option><option value="authorized_user">am authorised by its owner</option></select> and these credentials were issued to me.</label>
        </div>
        {error && <div className="error">{error}</div>}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={busy || !attested}>{busy ? "Saving…" : "Save"}</button>
        </div>
      </form>
    </Modal>
  );
}

function Channels({ device, onClose }: { device: Device & { channel_discovery?: string | null }; onClose: () => void }) {
  const [channels, setChannels] = useState<DeviceChannel[] | null>(null);
  const [names, setNames] = useState<Record<number, string>>({});
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [manual, setManual] = useState("4");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<PrivateCamera[] | null>(null);

  function applyList(list: DeviceChannel[]) {
    setChannels(list);
    setNames(Object.fromEntries(list.map((c) => [c.index, `${device.name} – ${c.name}`])));
    setSelected(new Set(list.filter((c) => !c.imported_camera_id).map((c) => c.index)));
  }

  async function discover() {
    setBusy(true);
    setError(null);
    try { applyList((await api<{ channels: DeviceChannel[] }>(`/api/devices/${device.id}/discover`, { method: "POST" })).channels); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  function manualList() {
    const n = Math.max(1, Math.min(64, Number(manual) || 1));
    applyList(Array.from({ length: n }, (_, i) => ({ index: i + 1, name: `Channel ${i + 1}`, main_profile: null, sub_profile: null, resolution: null, has_sub_stream: false, imported_camera_id: null })));
  }

  async function importNow() {
    setBusy(true);
    setError(null);
    try { setDone(await api<PrivateCamera[]>(`/api/devices/${device.id}/import`, { method: "POST", json: { channels: [...selected].map((i) => ({ index: i, name: names[i] })) } })); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <Modal title={`Channels on ${device.name}`} onClose={onClose}>
      {done ? (
        <>
          <div className="small muted">Added {done.length} channel cameras. The first channels were tested right away; run Diagnostics on the others.</div>
          {done.map((c) => (
            <div className="row card" key={c.id}>
              <span className={`dot s-${c.status}`} /><span className="grow ellipsis">{c.name}</span>
              <span className={`badge ${c.video?.available ? "public" : "neutral"}`}>{c.video?.available ? "📷 Snapshot view" : "No video yet"}</span>
            </div>
          ))}
          <button className="btn primary" onClick={onClose}>Done</button>
        </>
      ) : (
        <>
          <div className="row">
            {device.channel_discovery && <button className="btn primary" disabled={busy} onClick={discover}>{busy ? "Asking the recorder…" : "Discover channels"}</button>}
            <input className="input" style={{ width: 70 }} type="number" min={1} max={64} value={manual} onChange={(e) => setManual(e.target.value)} aria-label="Number of channels" />
            <button className="btn" onClick={manualList}>Enter manually</button>
          </div>
          {error && <div className="error">{error}</div>}
          {channels?.map((c) => (
            <div key={c.index} className="card">
              <label className="row">
                <input type="checkbox" disabled={!!c.imported_camera_id} checked={selected.has(c.index)}
                  onChange={(e) => { const s = new Set(selected); if (e.target.checked) s.add(c.index); else s.delete(c.index); setSelected(s); }} />
                <strong className="grow">CH{c.index}</strong>
                {c.imported_camera_id && <span className="small muted">Already added</span>}
              </label>
              {!c.imported_camera_id && <input className="input" value={names[c.index] ?? ""} onChange={(e) => setNames({ ...names, [c.index]: e.target.value })} aria-label={`Name for channel ${c.index}`} />}
            </div>
          ))}
          {channels && <button className="btn primary" disabled={busy || !selected.size} onClick={importNow}>{busy ? "Adding and testing…" : `Add ${selected.size} channel${selected.size === 1 ? "" : "s"}`}</button>}
        </>
      )}
    </Modal>
  );
}

export default function RecordersFree({ devices, sites, reload }: { devices: Device[]; sites: Site[]; reload: () => void }) {
  const [adding, setAdding] = useState(false);
  const [channels, setChannels] = useState<Device | null>(null);
  return (
    <>
      <button className="btn primary" onClick={() => setAdding(true)}>+ Add DVR / NVR</button>
      <div className="small muted">Register a recorder once; its channels become cameras that share the recorder&apos;s encrypted credentials.</div>
      {devices.map((d) => (
        <div className="card" key={d.id}>
          <div className="row">
            <span className={`dot s-${d.status}`} /><strong className="grow ellipsis">{d.name}</strong>
            <span className={`status-label s-${d.status}`}>{STATUS_TEXT[d.status] ?? d.status}</span>
          </div>
          <div className="small muted">{(d as Device & { brand_label?: string }).brand_label ?? d.vendor}{d.model ? ` · ${d.model}` : ""} · {d.channel_count} channels</div>
          {d.last_error && <div className="small" style={{ color: "#fca5a5" }}>{d.last_error}</div>}
          <div className="row">
            <button className="btn sm" onClick={() => setChannels(d)}>Channels</button>
            <button className="btn sm danger" onClick={async () => { if (confirm(`Delete ${d.name} and its ${d.channel_count} channel cameras?`)) { await api(`/api/devices/${d.id}`, { method: "DELETE" }); reload(); } }}>Delete</button>
          </div>
        </div>
      ))}
      {adding && <AddRecorder sites={sites} onClose={() => setAdding(false)} onCreated={(d) => { setAdding(false); reload(); setChannels(d); }} />}
      {channels && <Channels device={channels} onClose={() => { setChannels(null); reload(); }} />}
    </>
  );
}
