"use client";

import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { AnyCamera, PrivateCamera, PublicCamera, User } from "@/lib/types";
import AddCameraDialog from "./AddCameraDialog";
import CameraTile from "./CameraTile";
import DiagnosticsPanel from "./DiagnosticsPanel";

const MapView = dynamic(() => import("./MapView"), { ssr: false, loading: () => <div className="empty" style={{ height: "100%" }}>Loading map…</div> });

type Tab = "discover" | "mine" | "saved" | "recent" | "diagnostics";
type Layout = "l1" | "l4" | "l9";
const MAX_TILES: Record<Layout, number> = { l1: 1, l4: 4, l9: 9 };
const DEFAULT_CENTER: [number, number] = [37.7749, -122.4194];

function CameraRow({ cam, selected, onOpen, extra }: { cam: AnyCamera; selected: boolean; onOpen: () => void; extra?: React.ReactNode }) {
  const isPublic = cam.kind === "public";
  return (
    <div className={`cam-item ${selected ? "selected" : ""}`} onClick={onOpen} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onOpen()}>
      <div className="row">
        <span className={`dot s-${cam.status}`} title={cam.status} />
        <span className="title grow">{cam.name}</span>
        <span className={`badge ${cam.kind}`}>{isPublic ? "Public" : "Private"}</span>
      </div>
      <div className="meta">
        {isPublic ? (
          <>
            <span>{(cam as PublicCamera).camera_type}</span>·<span>{(cam as PublicCamera).stream_type}</span>·
            <span>{(cam as PublicCamera).source_name}</span>
            {(cam as PublicCamera).distance_km != null && <span>· {(cam as PublicCamera).distance_km!.toFixed(1)} km</span>}
          </>
        ) : (
          <>
            <span>{(cam as PrivateCamera).protocol.toUpperCase()}</span>·<span>{(cam as PrivateCamera).host}</span>·
            <span>{(cam as PrivateCamera).authorization_basis === "owner" ? "owned" : "authorised"}</span>
          </>
        )}
        <span>· {cam.status}</span>
        {extra && <span className="grow" style={{ display: "flex", justifyContent: "flex-end", gap: 4 }} onClick={(e) => e.stopPropagation()}>{extra}</span>}
      </div>
      {!isPublic && (cam as PrivateCamera).last_error && <div className="small" style={{ color: "#fca5a5" }}>{(cam as PrivateCamera).last_error}</div>}
    </div>
  );
}

export default function Dashboard() {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [tab, setTab] = useState<Tab>("discover");
  const [center, setCenter] = useState<[number, number]>(DEFAULT_CENTER);
  const [mapCenter, setMapCenter] = useState<[number, number]>(DEFAULT_CENTER);
  const [zoomKey, setZoomKey] = useState(0);
  const [radius, setRadius] = useState(15);
  const [typeFilter, setTypeFilter] = useState("");
  const [userLoc, setUserLoc] = useState<[number, number] | null>(null);
  const [geoMsg, setGeoMsg] = useState<string | null>(null);
  const [publicCams, setPublicCams] = useState<PublicCamera[]>([]);
  const [privateCams, setPrivateCams] = useState<PrivateCamera[]>([]);
  const [saved, setSaved] = useState<PublicCamera[]>([]);
  const [recent, setRecent] = useState<{ viewed_at: string; camera: AnyCamera }[]>([]);
  const [tiles, setTiles] = useState<AnyCamera[]>([]);
  const [layout, setLayout] = useState<Layout>("l4");
  const [selected, setSelected] = useState<string | null>(null);
  const [diagTarget, setDiagTarget] = useState<AnyCamera | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    api<User>("/api/auth/me").then(setUser).catch(() => router.replace("/login"));
  }, [router]);

  const loadNearby = useCallback(async (c: [number, number], r: number, t: string) => {
    const q = new URLSearchParams({ lat: String(c[0]), lon: String(c[1]), radius_km: String(r) });
    if (t) q.set("camera_type", t);
    try {
      setPublicCams(await api<PublicCamera[]>(`/api/public-cameras/nearby?${q}`));
    } catch (e) {
      setMsg((e as Error).message);
    }
  }, []);

  const loadPrivate = useCallback(() => api<PrivateCamera[]>("/api/cameras").then(setPrivateCams).catch(() => {}), []);
  const loadSaved = useCallback(() => api<PublicCamera[]>("/api/saved").then(setSaved).catch(() => {}), []);
  const loadRecent = useCallback(() => api<{ viewed_at: string; camera: AnyCamera }[]>("/api/history").then(setRecent).catch(() => {}), []);

  useEffect(() => {
    if (!user) return;
    loadPrivate();
    loadSaved();
    loadRecent();
  }, [user, loadPrivate, loadSaved, loadRecent]);

  useEffect(() => {
    if (user) loadNearby(center, radius, typeFilter);
  }, [user, center, radius, typeFilter, loadNearby]);

  function locate() {
    if (!("geolocation" in navigator)) return setGeoMsg("Geolocation is not supported by this browser.");
    setGeoMsg("Waiting for permission…");
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const c: [number, number] = [pos.coords.latitude, pos.coords.longitude];
        setUserLoc(c);
        setCenter(c);
        setZoomKey((k) => k + 1);
        setGeoMsg(`Location accuracy ±${Math.round(pos.coords.accuracy)} m. Your location is only used in your browser and in search requests.`);
      },
      (err) => setGeoMsg(err.code === err.PERMISSION_DENIED ? "Location permission denied – pan the map and use “Search this area”." : `Location unavailable: ${err.message}`),
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 },
    );
  }

  async function discover() {
    setBusy(true);
    setMsg(null);
    try {
      const res = await api<{ imported: Record<string, number>; errors: Record<string, string>; cameras: PublicCamera[] }>("/api/public-cameras/discover", {
        method: "POST",
        json: { lat: center[0], lon: center[1], radius_km: Math.min(radius, 50) },
      });
      setPublicCams(typeFilter ? res.cameras.filter((c) => c.camera_type === typeFilter) : res.cameras);
      const imported = Object.entries(res.imported).map(([k, v]) => `${k}: ${v}`).join(", ");
      const errs = Object.entries(res.errors).map(([k, v]) => `${k} failed (${v})`).join("; ");
      setMsg(`Live directory scan complete${imported ? ` – ${imported}` : ""}${errs ? `. ${errs}` : ""}`);
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function open(cam: AnyCamera) {
    setSelected(cam.id);
    setTiles((t) => {
      if (t.some((x) => x.id === cam.id)) return t;
      const max = MAX_TILES[layout];
      const next = [...t, cam];
      if (next.length > max) {
        if (layout !== "l9") setLayout(next.length <= 4 ? "l4" : "l9");
        return next.slice(-9);
      }
      return next;
    });
  }

  async function toggleSave(cam: PublicCamera) {
    await api(`/api/saved/${cam.id}`, { method: cam.saved ? "DELETE" : "PUT" });
    setPublicCams((list) => list.map((c) => (c.id === cam.id ? { ...c, saved: !cam.saved } : c)));
    loadSaved();
  }

  async function removePrivate(cam: PrivateCamera) {
    if (!confirm(`Delete “${cam.name}” and its encrypted credentials?`)) return;
    await api(`/api/cameras/${cam.id}`, { method: "DELETE" });
    setTiles((t) => t.filter((x) => x.id !== cam.id));
    loadPrivate();
  }

  async function logout() {
    await api("/api/auth/logout", { method: "POST" }).catch(() => {});
    router.replace("/login");
  }

  const diagCandidates = useMemo(() => {
    const m = new Map<string, AnyCamera>();
    [...tiles, ...privateCams, ...saved, ...recent.map((r) => r.camera), ...publicCams.slice(0, 50)].forEach((c) => m.set(`${c.kind}:${c.id}`, c));
    return [...m.values()];
  }, [tiles, privateCams, saved, recent, publicCams]);

  if (!user) return <div className="auth-wrap muted">Loading…</div>;

  const counts = { traffic: publicCams.filter((c) => c.camera_type === "traffic").length, webcam: publicCams.filter((c) => c.camera_type === "webcam").length };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand"><div className="brand-logo">◉</div> CameraScope</div>
        <span className="badge public">Public feeds</span>
        <span className="badge private">Private (authorised) feeds</span>
        <div className="spacer" />
        <span className="muted small">{user.display_name}{user.is_admin ? " · admin" : ""}</span>
        <button className="btn sm" onClick={logout}>Sign out</button>
      </header>

      <div className="main">
        <aside className="sidebar">
          <nav className="tabs">
            {([
              ["discover", "Discover"],
              ["mine", `My cameras (${privateCams.length})`],
              ["saved", `Saved (${saved.length})`],
              ["recent", "Recent"],
              ["diagnostics", "Diagnostics"],
            ] as [Tab, string][]).map(([id, label]) => (
              <button key={id} className={`tab ${tab === id ? "active" : ""}`} onClick={() => { setTab(id); if (id === "recent") loadRecent(); }}>{label}</button>
            ))}
          </nav>
          <div className="panel-body">
            {tab === "discover" && (
              <>
                <div className="row">
                  <button className="btn primary grow" onClick={locate}>📍 Use my location</button>
                  <button className="btn grow" onClick={() => { setCenter(mapCenter); }}>Search this area</button>
                </div>
                {geoMsg && <div className="small muted">{geoMsg}</div>}
                <label className="field">
                  Radius: {radius} km
                  <input type="range" min={1} max={200} value={radius} onChange={(e) => setRadius(Number(e.target.value))} onMouseUp={() => setZoomKey((k) => k + 1)} onTouchEnd={() => setZoomKey((k) => k + 1)} />
                </label>
                <div className="row">
                  <select className="select grow" value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
                    <option value="">All public types</option>
                    <option value="traffic">Traffic cameras</option>
                    <option value="webcam">Webcams</option>
                    <option value="public_ip">Public IP cameras</option>
                  </select>
                  <button className="btn" onClick={discover} disabled={busy} title="Query OpenStreetMap / Windy directories for this area">{busy ? "Scanning…" : "Scan directories"}</button>
                </div>
                <div className="small muted">
                  {publicCams.length} public cameras within {radius} km ({counts.traffic} traffic, {counts.webcam} webcams). Only feeds their
                  operators publish openly are listed.
                </div>
                {msg && <div className="notice small">{msg}</div>}
                <div className="cam-list">
                  {publicCams.map((c) => (
                    <CameraRow
                      key={c.id}
                      cam={c}
                      selected={selected === c.id}
                      onOpen={() => open(c)}
                      extra={<button className="btn sm" onClick={() => toggleSave(c)} title={c.saved ? "Remove from saved" : "Save"}>{c.saved ? "★" : "☆"}</button>}
                    />
                  ))}
                </div>
              </>
            )}

            {tab === "mine" && (
              <>
                <button className="btn primary" onClick={() => setShowAdd(true)}>+ Add a camera you own</button>
                <div className="small muted">
                  Private cameras are visible only to you. They are reached through the secure gateway from wherever they are
                  hosted – home network, mobile/LTE router or another site – using credentials only you supplied.
                </div>
                <div className="cam-list">
                  {privateCams.length === 0 && <div className="empty">No private cameras yet.</div>}
                  {privateCams.map((c) => (
                    <CameraRow
                      key={c.id}
                      cam={c}
                      selected={selected === c.id}
                      onOpen={() => open(c)}
                      extra={
                        <>
                          <button className="btn sm" onClick={() => { setDiagTarget(c); setTab("diagnostics"); }}>Test</button>
                          <button className="btn sm danger" onClick={() => removePrivate(c)}>Delete</button>
                        </>
                      }
                    />
                  ))}
                </div>
              </>
            )}

            {tab === "saved" && (
              <div className="cam-list">
                {saved.length === 0 && <div className="empty">Save public cameras with ☆ to find them here.</div>}
                {saved.map((c) => (
                  <CameraRow key={c.id} cam={c} selected={selected === c.id} onOpen={() => open(c)} extra={<button className="btn sm" onClick={() => toggleSave(c)}>★</button>} />
                ))}
              </div>
            )}

            {tab === "recent" && (
              <>
                <div className="row">
                  <span className="grow small muted">Last 20 cameras you viewed</span>
                  <button className="btn sm" onClick={async () => { await api("/api/history", { method: "DELETE" }); loadRecent(); }}>Clear</button>
                </div>
                <div className="cam-list">
                  {recent.length === 0 && <div className="empty">Nothing viewed yet.</div>}
                  {recent.map((r) => (
                    <CameraRow key={`${r.camera.kind}${r.camera.id}`} cam={r.camera} selected={selected === r.camera.id} onOpen={() => open(r.camera)} extra={<span className="small">{new Date(r.viewed_at).toLocaleString()}</span>} />
                  ))}
                </div>
              </>
            )}

            {tab === "diagnostics" && (
              <DiagnosticsPanel target={diagTarget} candidates={diagCandidates} onPick={setDiagTarget} onRan={loadPrivate} />
            )}
          </div>
        </aside>

        <section className="content">
          <div className="map-wrap">
            <MapView
              center={center}
              zoomKey={zoomKey}
              radiusKm={radius}
              userLocation={userLoc}
              publicCameras={publicCams}
              privateCameras={privateCams}
              selectedId={selected}
              onSelect={open}
              onCenterChange={setMapCenter}
            />
            <div className="legend">
              <span className="row"><span className="dot" style={{ background: "#38bdf8" }} /> Public</span>
              <span className="row"><span className="dot" style={{ background: "#f59e0b" }} /> Private</span>
              <span className="row"><span className="dot" style={{ background: "#ef4444" }} /> Offline</span>
            </div>
          </div>
          <div className="viewer">
            <div className="viewer-bar">
              <strong className="grow">Live viewer</strong>
              <span className="small muted">{tiles.length} open</span>
              {(["l1", "l4", "l9"] as Layout[]).map((l) => (
                <button key={l} className={`btn sm ${layout === l ? "primary" : ""}`} onClick={() => { setLayout(l); setTiles((t) => t.slice(-MAX_TILES[l])); }}>
                  {l === "l1" ? "1×1" : l === "l4" ? "2×2" : "3×3"}
                </button>
              ))}
              <button className="btn sm" onClick={() => setTiles([])} disabled={!tiles.length}>Close all</button>
            </div>
            <div className={`grid ${layout}`}>
              {tiles.length === 0 && <div className="empty" style={{ gridColumn: "1 / -1" }}>Select a camera on the map or in the list to start watching.</div>}
              {tiles.map((c) => (
                <CameraTile
                  key={`${c.kind}:${c.id}`}
                  camera={c}
                  onClose={() => setTiles((t) => t.filter((x) => x.id !== c.id))}
                  onDiagnose={(cam) => { setDiagTarget(cam); setTab("diagnostics"); }}
                />
              ))}
            </div>
          </div>
        </section>
      </div>

      {showAdd && (
        <AddCameraDialog
          defaultLocation={userLoc}
          onClose={() => setShowAdd(false)}
          onCreated={(cam) => { setShowAdd(false); loadPrivate(); open(cam); }}
        />
      )}
    </div>
  );
}
