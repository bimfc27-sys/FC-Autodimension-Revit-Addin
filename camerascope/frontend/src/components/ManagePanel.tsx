"use client";

import { FormEvent, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Transport, getTransport, setTransport } from "@/lib/prefs";
import type { CameraGroup, Device, DeviceChannel, Enrollment, Gateway, PrivateCamera, Site, SiteMember } from "@/lib/types";
import { STATUS_TEXT } from "./CamerasPanel";

type Sub = "gateways" | "recorders" | "buildings" | "groups" | "settings";

interface Props {
  gateways: Gateway[];
  devices: Device[];
  sites: Site[];
  groups: CameraGroup[];
  cameras: PrivateCamera[];
  reload: () => void;
  onAddCamera: () => void;
  onDiagnose: (cam: PrivateCamera) => void;
}

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "never");

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-label={title}>
        <div className="row">
          <h3 className="grow" style={{ margin: 0 }}>{title}</h3>
          <button className="btn icon sm" onClick={onClose} aria-label="Close">✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ gateways

function EnrollmentInfo({ e }: { e: Enrollment }) {
  const origin = typeof window !== "undefined" ? window.location.origin : "https://your-camerascope-domain";
  const env = `CLOUD_URL=${origin}\nENROLLMENT_CODE=${e.enrollment_code}\n# the camera network(s) this gateway may reach, e.g. your Wi-Fi LAN:\nALLOWED_SUBNETS=192.168.1.0/24`;
  return (
    <>
      <div className="notice small">
        Install the gateway on an always-on device inside the camera network (Raspberry Pi, NAS, mini-PC). It only makes an
        <strong> outbound</strong> encrypted connection to CameraScope – no router port-forwarding is needed. The enrollment code is
        shown once and expires {new Date(e.expires_at).toLocaleTimeString()}.
      </div>
      <label className="field">1. Copy the <code>gateway/</code> folder of CameraScope to that device and create <code>.env</code>:
        <div className="code-box">{env}</div>
      </label>
      <label className="field">2. Start it:
        <div className="code-box">./generate-relay-secret.sh &amp;&amp; docker compose up -d</div>
      </label>
      <div className="small muted">The gateway appears as Online here within a few seconds. Then add cameras or recorders and pick this gateway.</div>
    </>
  );
}

function Gateways({ gateways, reload }: { gateways: Gateway[]; reload: () => void }) {
  const [name, setName] = useState("");
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function create(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      setEnrollment(await api<Enrollment>("/api/gateways", { method: "POST", json: { name } }));
      setName("");
      reload();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <>
      <div className="small muted">
        One gateway per camera network you are authorised to use (home Wi-Fi, office, NVR network…). Cameras reachable from the
        internet or through your VPN can be added without a gateway (“Direct / VPN”).
      </div>
      <form className="row" onSubmit={create}>
        <input className="input grow" placeholder="Gateway name, e.g. Home Wi-Fi" value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} />
        <button className="btn primary">Add gateway</button>
      </form>
      {error && <div className="error">{error}</div>}
      {gateways.map((g) => (
        <div className="card" key={g.id}>
          <div className="row">
            <span className={`dot s-${g.online ? "online" : g.status === "pending" ? "unknown" : "offline"}`} />
            <strong className="grow ellipsis">{g.name}</strong>
            <span className={`status-label s-${g.online ? "online" : "offline"}`}>{g.online ? "Online" : g.status === "pending" ? "Waiting for install" : "Offline"}</span>
          </div>
          <div className="small muted">
            {g.camera_count} cameras · {g.device_count} recorders · last seen {when(g.last_seen_at)}
            {g.agent_version && ` · agent ${g.agent_version}`}
          </div>
          {g.allowed_subnets.length > 0 && <div className="small">Allowed networks: {g.allowed_subnets.join(", ")}</div>}
          <div className="row">
            <button className="btn sm" onClick={async () => { if (confirm("Revoke this gateway's credentials and create a new enrollment code?")) { setEnrollment(await api<Enrollment>(`/api/gateways/${g.id}/reenroll`, { method: "POST" })); reload(); } }}>
              Re-enroll
            </button>
            <button className="btn sm danger" onClick={async () => {
              if (!confirm(`Delete gateway “${g.name}”?`)) return;
              try { await api(`/api/gateways/${g.id}`, { method: "DELETE" }); reload(); } catch (err) { alert((err as Error).message); }
            }}>Delete</button>
          </div>
        </div>
      ))}
      {enrollment && (
        <Modal title={`Install gateway “${enrollment.gateway.name}”`} onClose={() => setEnrollment(null)}>
          <EnrollmentInfo e={enrollment} />
        </Modal>
      )}
    </>
  );
}

// ------------------------------------------------------------------ recorders

function AddRecorder({ gateways, sites, onClose, onCreated }: { gateways: Gateway[]; sites: Site[]; onClose: () => void; onCreated: (d: Device) => void }) {
  const [f, setF] = useState({ name: "", protocol: "onvif", vendor: "hikvision", host: "", port: "", rtsp_port: "554", username: "", password: "", gateway_id: gateways[0]?.id ?? "", site_id: "", basis: "owner" });
  const [attested, setAttested] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const d = await api<Device>("/api/devices", {
        method: "POST",
        json: {
          name: f.name, kind: "nvr", protocol: f.protocol, vendor: f.protocol === "onvif" ? "generic" : f.vendor, host: f.host,
          port: f.port ? Number(f.port) : null, rtsp_port: f.rtsp_port ? Number(f.rtsp_port) : null,
          credentials: { username: f.username, password: f.password }, gateway_id: f.gateway_id || null, site_id: f.site_id || null,
          authorization_basis: f.basis, authorization_attested: attested,
        },
      });
      onCreated(d);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Add DVR / NVR" onClose={onClose}>
      <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 10 }} autoComplete="off">
        <label className="field">Name<input className="input" value={f.name} onChange={set("name")} required maxLength={200} placeholder="Warehouse NVR" /></label>
        <div className="grid2">
          <label className="field">Channel discovery
            <select className="select" value={f.protocol} onChange={set("protocol")}>
              <option value="onvif">ONVIF (most recorders)</option>
              <option value="vendor_api">Manufacturer API</option>
            </select>
          </label>
          {f.protocol === "vendor_api" && (
            <label className="field">Manufacturer
              <select className="select" value={f.vendor} onChange={set("vendor")}>
                <option value="hikvision">Hikvision (ISAPI)</option>
                <option value="dahua">Dahua</option>
                <option value="amcrest">Amcrest</option>
              </select>
            </label>
          )}
        </div>
        <label className="field">Reached through
          <select className="select" value={f.gateway_id} onChange={set("gateway_id")}>
            {gateways.map((g) => <option key={g.id} value={g.id}>Gateway: {g.name}{g.online ? "" : " (offline)"}</option>)}
            <option value="">Direct / VPN (recorder reachable from the cloud)</option>
          </select>
        </label>
        <div className="grid2">
          <label className="field">Host (address on that network)<input className="input" value={f.host} onChange={(e) => setF({ ...f, host: e.target.value.trim() })} required placeholder="192.168.1.64" /></label>
          <label className="field">HTTP / ONVIF port<input className="input" type="number" value={f.port} onChange={set("port")} placeholder="80" /></label>
        </div>
        <label className="field">RTSP port<input className="input" type="number" value={f.rtsp_port} onChange={set("rtsp_port")} /></label>
        <div className="grid2">
          <label className="field">Recorder username<input className="input" value={f.username} onChange={set("username")} autoComplete="off" /></label>
          <label className="field">Recorder password<input className="input" type="password" value={f.password} onChange={set("password")} autoComplete="new-password" /></label>
        </div>
        {sites.some((s) => s.role === "manager") && (
          <label className="field">Building (optional – shares imported channels with its members)
            <select className="select" value={f.site_id} onChange={set("site_id")}>
              <option value="">Not shared</option>
              {sites.filter((s) => s.role === "manager").map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
        )}
        <div className="attest">
          <input id="attest-nvr" type="checkbox" checked={attested} onChange={(e) => setAttested(e.target.checked)} />
          <label htmlFor="attest-nvr" className="small">
            I confirm that I{" "}
            <select className="select mini" value={f.basis} onChange={set("basis")}>
              <option value="owner">own this recorder</option>
              <option value="authorized_user">have explicit authorisation from its owner</option>
            </select>{" "}
            and that these credentials were issued to me.
          </label>
        </div>
        {error && <div className="error">{error}</div>}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={busy || !attested}>{busy ? "Saving…" : "Save & discover channels"}</button>
        </div>
      </form>
    </Modal>
  );
}

function Channels({ device, onClose, onImported }: { device: Device; onClose: () => void; onImported: () => void }) {
  const [channels, setChannels] = useState<DeviceChannel[] | null>(null);
  const [names, setNames] = useState<Record<number, string>>({});
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);

  useEffect(() => {
    api<{ channels: DeviceChannel[] }>(`/api/devices/${device.id}/discover`, { method: "POST" })
      .then((r) => {
        setChannels(r.channels);
        setNames(Object.fromEntries(r.channels.map((c) => [c.index, `${device.name} – ${c.name}`])));
        setSelected(new Set(r.channels.filter((c) => !c.imported_camera_id).map((c) => c.index)));
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setBusy(false));
  }, [device.id, device.name]);

  async function importNow() {
    setBusy(true);
    try {
      await api(`/api/devices/${device.id}/import`, { method: "POST", json: { channels: [...selected].map((i) => ({ index: i, name: names[i] })) } });
      onImported();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <Modal title={`Channels on ${device.name}`} onClose={onClose}>
      <div className="small muted">Discovered through {device.protocol === "onvif" ? "ONVIF" : `${device.vendor} API`} {device.gateway ? `via gateway “${device.gateway.name}”` : "directly"}. If discovery is unavailable, add channels manually with “+ Add camera”.</div>
      {busy && !channels && <div className="muted">Asking the recorder for its channels…</div>}
      {error && <div className="error">{error}</div>}
      {channels?.map((c) => (
        <div key={c.index} className="card">
          <label className="row">
            <input type="checkbox" disabled={!!c.imported_camera_id} checked={selected.has(c.index)}
              onChange={(e) => { const s = new Set(selected); if (e.target.checked) s.add(c.index); else s.delete(c.index); setSelected(s); }} />
            <strong className="grow">CH{c.index}</strong>
            <span className="small muted">{c.resolution ?? ""}{c.has_sub_stream ? " · HD+SD" : ""}</span>
          </label>
          {c.imported_camera_id ? <div className="small muted">Already added</div> :
            <input className="input" value={names[c.index] ?? ""} onChange={(e) => setNames({ ...names, [c.index]: e.target.value })} aria-label={`Name for channel ${c.index}`} />}
        </div>
      ))}
      {channels && (
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button className="btn" onClick={onClose}>Close</button>
          <button className="btn primary" disabled={busy || selected.size === 0} onClick={importNow}>Add {selected.size} channel{selected.size === 1 ? "" : "s"}</button>
        </div>
      )}
    </Modal>
  );
}

function Recorders({ devices, gateways, sites, reload }: { devices: Device[]; gateways: Gateway[]; sites: Site[]; reload: () => void }) {
  const [adding, setAdding] = useState(false);
  const [discovering, setDiscovering] = useState<Device | null>(null);
  return (
    <>
      <button className="btn primary" onClick={() => setAdding(true)}>+ Add DVR / NVR</button>
      <div className="small muted">Register a recorder once; its channels become cameras that share the recorder&apos;s encrypted credentials.</div>
      {devices.map((d) => (
        <div className="card" key={d.id}>
          <div className="row">
            <span className={`dot s-${d.status}`} />
            <strong className="grow ellipsis">{d.name}</strong>
            <span className={`status-label s-${d.status}`}>{STATUS_TEXT[d.status] ?? d.status}</span>
          </div>
          <div className="small muted">
            {d.model ?? d.vendor} · {d.channel_count} channels · {d.gateway ? `⇄ ${d.gateway.name}` : "☁ Direct / VPN"}
          </div>
          {d.last_error && <div className="small" style={{ color: "#fca5a5" }}>{d.last_error}</div>}
          <div className="row">
            <button className="btn sm" onClick={() => setDiscovering(d)}>Discover channels</button>
            <button className="btn sm danger" onClick={async () => { if (confirm(`Delete ${d.name} and its ${d.channel_count} channel cameras?`)) { await api(`/api/devices/${d.id}`, { method: "DELETE" }); reload(); } }}>Delete</button>
          </div>
        </div>
      ))}
      {adding && <AddRecorder gateways={gateways} sites={sites} onClose={() => setAdding(false)} onCreated={(d) => { setAdding(false); reload(); setDiscovering(d); }} />}
      {discovering && <Channels device={discovering} onClose={() => { setDiscovering(null); reload(); }} onImported={() => { setDiscovering(null); reload(); }} />}
    </>
  );
}

// ------------------------------------------------------------------ buildings

function Members({ site, onClose, reload }: { site: Site; onClose: () => void; reload: () => void }) {
  const [members, setMembers] = useState<SiteMember[]>([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"viewer" | "manager">("viewer");
  const [error, setError] = useState<string | null>(null);
  const load = () => api<SiteMember[]>(`/api/sites/${site.id}/members`).then(setMembers).catch((e) => setError((e as Error).message));
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function add(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api(`/api/sites/${site.id}/members`, { method: "POST", json: { email, role } });
      setEmail("");
      load();
      reload();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <Modal title={`${site.name} – people`} onClose={onClose}>
      <div className="small muted"><strong>Viewers</strong> can watch the building&apos;s cameras. <strong>Managers</strong> can also add their own cameras to the building and manage people. Nobody but a camera&apos;s owner can see its address or credentials.</div>
      {site.role === "manager" && (
        <form className="row" onSubmit={add}>
          <input className="input grow" type="email" placeholder="person@example.com" value={email} onChange={(e) => setEmail(e.target.value)} required />
          <select className="select mini" value={role} onChange={(e) => setRole(e.target.value as "viewer")}>
            <option value="viewer">Viewer</option>
            <option value="manager">Manager</option>
          </select>
          <button className="btn primary">Add</button>
        </form>
      )}
      {error && <div className="error">{error}</div>}
      {members.map((m) => (
        <div className="row card" key={m.user_id}>
          <div className="grow ellipsis"><strong>{m.display_name}</strong><div className="small muted ellipsis">{m.email}</div></div>
          <span className="badge neutral">{m.role}</span>
          {site.role === "manager" && (
            <button className="btn sm danger" onClick={async () => { try { await api(`/api/sites/${site.id}/members/${m.user_id}`, { method: "DELETE" }); load(); reload(); } catch (e) { setError((e as Error).message); } }}>Remove</button>
          )}
        </div>
      ))}
    </Modal>
  );
}

function Buildings({ sites, cameras, reload }: { sites: Site[]; cameras: PrivateCamera[]; reload: () => void }) {
  const [name, setName] = useState("");
  const [members, setMembers] = useState<Site | null>(null);
  const mine = cameras.filter((c) => c.is_owner);

  async function assign(cam: PrivateCamera, siteId: string) {
    try {
      await api(`/api/cameras/${cam.id}`, { method: "PATCH", json: { site_id: siteId || null } });
      reload();
    } catch (e) {
      alert((e as Error).message);
    }
  }

  return (
    <>
      <div className="small muted">A building groups cameras and the people allowed to watch them. Cameras appear for its members under “Building cameras”.</div>
      <form className="row" onSubmit={async (e) => { e.preventDefault(); await api("/api/sites", { method: "POST", json: { name } }); setName(""); reload(); }}>
        <input className="input grow" placeholder="Building name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={200} />
        <button className="btn primary">Create</button>
      </form>
      {sites.map((s) => (
        <div className="card" key={s.id}>
          <div className="row">
            <strong className="grow ellipsis">🏢 {s.name}</strong>
            <span className="badge neutral">{s.role}</span>
          </div>
          <div className="small muted">{s.camera_count} cameras · {s.member_count} people</div>
          <div className="row">
            <button className="btn sm" onClick={() => setMembers(s)}>People</button>
            {s.role === "manager" && <button className="btn sm danger" onClick={async () => { if (confirm(`Delete building ${s.name}? Cameras stay with their owners.`)) { await api(`/api/sites/${s.id}`, { method: "DELETE" }); reload(); } }}>Delete</button>}
          </div>
        </div>
      ))}
      {sites.some((s) => s.role === "manager") && mine.length > 0 && (
        <>
          <div className="section-title">Assign your cameras<span className="line" /></div>
          {mine.map((c) => (
            <div className="row" key={c.id}>
              <span className="grow ellipsis small">{c.name}</span>
              <select className="select mini" value={c.site?.id ?? ""} onChange={(e) => assign(c, e.target.value)} aria-label={`Building for ${c.name}`}>
                <option value="">Not shared</option>
                {sites.filter((s) => s.role === "manager").map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </div>
          ))}
        </>
      )}
      {members && <Members site={members} onClose={() => setMembers(null)} reload={reload} />}
    </>
  );
}

// ------------------------------------------------------------------ groups

function Groups({ groups, reload }: { groups: CameraGroup[]; reload: () => void }) {
  const [name, setName] = useState("");
  return (
    <>
      <div className="small muted">Groups are your own views (e.g. “Entrances”). Add cameras to a group with ＋ in the camera list.</div>
      <form className="row" onSubmit={async (e) => { e.preventDefault(); await api("/api/groups", { method: "POST", json: { name } }); setName(""); reload(); }}>
        <input className="input grow" placeholder="Group name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} />
        <button className="btn primary">Create</button>
      </form>
      {groups.map((g) => (
        <div className="card" key={g.id}>
          <div className="row">
            <strong className="grow ellipsis">{g.name}</strong>
            <span className="small muted">{g.cameras.length} cameras</span>
            <button className="btn sm" onClick={async () => { const n = prompt("Rename group", g.name); if (n) { await api(`/api/groups/${g.id}`, { method: "PATCH", json: { name: n } }); reload(); } }}>Rename</button>
            <button className="btn sm danger" onClick={async () => { if (confirm(`Delete group ${g.name}?`)) { await api(`/api/groups/${g.id}`, { method: "DELETE" }); reload(); } }}>Delete</button>
          </div>
          <div className="chips">
            {g.cameras.map((c) => (
              <span key={c.id} className="badge neutral">
                {c.name}
                <button className="btn icon sm" style={{ minHeight: 0, padding: "0 4px" }} aria-label={`Remove ${c.name}`} onClick={async () => {
                  await api(`/api/groups/${g.id}/items`, { method: "PUT", json: { items: g.cameras.filter((x) => x.id !== c.id).map((x) => ({ camera_kind: x.kind, camera_id: x.id })) } });
                  reload();
                }}>✕</button>
              </span>
            ))}
          </div>
        </div>
      ))}
    </>
  );
}

function Accounts() {
  const [users, setUsers] = useState<{ id: string; email: string; display_name: string; is_admin: boolean }[] | null>(null);
  const [f, setF] = useState({ email: "", display_name: "", password: "" });
  const [msg, setMsg] = useState<string | null>(null);
  const load = () => api<typeof users>("/api/auth/users").then(setUsers).catch(() => setUsers(null));
  useEffect(() => { load(); }, []);
  if (users === null) return null; // not an administrator
  return (
    <div className="card">
      <strong>Accounts (administrator)</strong>
      <div className="small muted">Create logins for family or colleagues, then share cameras with them through a Building.</div>
      {users.map((u) => <div key={u.id} className="small">{u.display_name} · {u.email}{u.is_admin ? " · admin" : ""}</div>)}
      <form style={{ display: "flex", flexDirection: "column", gap: 6 }} onSubmit={async (e) => {
        e.preventDefault();
        setMsg(null);
        try { await api("/api/auth/users", { method: "POST", json: f }); setF({ email: "", display_name: "", password: "" }); load(); setMsg("Account created."); }
        catch (err) { setMsg((err as Error).message); }
      }}>
        <input className="input" placeholder="Name" value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} required />
        <input className="input" type="email" placeholder="E-mail" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} required />
        <input className="input" type="password" placeholder="Initial password (12+ chars)" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} required minLength={12} autoComplete="new-password" />
        <button className="btn primary">Create account</button>
        {msg && <div className="small">{msg}</div>}
      </form>
    </div>
  );
}

function Settings() {
  const [t, setT] = useState<Transport>("auto");
  const [server, setServer] = useState<string>("");
  useEffect(() => {
    setT(getTransport());
    api<{ streaming_mode: string }>("/api/client-config").then((c) => setServer(c.streaming_mode)).catch(() => {});
  }, []);
  return (
    <>
      <div className="card">
        <strong>Video transport on this device</strong>
        <select className="select" value={t} onChange={(e) => { setT(e.target.value as Transport); setTransport(e.target.value as Transport); }} aria-label="Video transport">
          <option value="auto">Automatic – WebRTC (lowest delay), HLS fallback</option>
          <option value="hls">HLS only – for public tunnel URLs (Tailscale Funnel / Cloudflare)</option>
        </select>
        <div className="small muted">
          Choose “HLS only” when you open CameraScope through a public tunnel without the Tailscale app: WebRTC media can&apos;t pass
          through HTTPS-only tunnels. Applies to newly opened tiles.
        </div>
        {server === "hls" && <div className="notice small">This server is configured for HLS-only streaming (STREAMING_MODE=hls).</div>}
      </div>
      <Accounts />
    </>
  );
}

export default function ManagePanel(p: Props) {
  const [sub, setSub] = useState<Sub>("gateways");
  const offline = p.gateways.filter((g) => !g.online && g.status !== "pending").length;
  return (
    <>
      <div className="subtabs">
        {([["gateways", `Gateways${offline ? ` ⚠${offline}` : ""}`], ["recorders", "DVR / NVR"], ["buildings", "Buildings"], ["groups", "Groups"], ["settings", "Settings"]] as [Sub, string][]).map(([id, label]) => (
          <button key={id} className={`btn sm ${sub === id ? "primary" : ""}`} onClick={() => setSub(id)}>{label}</button>
        ))}
        <button className="btn sm" onClick={p.onAddCamera}>+ Camera</button>
      </div>
      {sub === "gateways" && <Gateways gateways={p.gateways} reload={p.reload} />}
      {sub === "recorders" && <Recorders devices={p.devices} gateways={p.gateways} sites={p.sites} reload={p.reload} />}
      {sub === "buildings" && <Buildings sites={p.sites} cameras={p.cameras} reload={p.reload} />}
      {sub === "groups" && <Groups groups={p.groups} reload={p.reload} />}
      {sub === "settings" && <Settings />}
    </>
  );
}
