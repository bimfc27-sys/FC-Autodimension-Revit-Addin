"use client";

import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { AnyCamera, CameraGroup, Device, Gateway, PrivateCamera, PublicCamera, Site, User } from "@/lib/types";
import AddCameraDialog from "./AddCameraDialog";
import CamerasPanel, { CameraRow } from "./CamerasPanel";
import CameraTile from "./CameraTile";
import DiagnosticsPanel from "./DiagnosticsPanel";
import ManagePanel from "./ManagePanel";

const MapView = dynamic(() => import("./MapView"), { ssr: false, loading: () => <div className="empty" style={{ height: "100%" }}>Loading map…</div> });

type View = "live" | "cameras" | "discover" | "manage" | "diagnostics";
type Layout = "l1" | "l4" | "l9";
const MAX_TILES: Record<Layout, number> = { l1: 1, l4: 4, l9: 9 };
const DEFAULT_CENTER: [number, number] = [37.7749, -122.4194];
const POLL_MS = 20000;
const key = (c: { kind: string; id: string }) => `${c.kind}:${c.id}`;

function useIsDesktop() {
  const [d, setD] = useState(true);
  useEffect(() => {
    const m = window.matchMedia("(min-width: 900px)");
    const on = () => setD(m.matches);
    on();
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return d;
}

function GroupPicker({ cam, groups, onClose, reload }: { cam: AnyCamera; groups: CameraGroup[]; onClose: () => void; reload: () => void }) {
  const [name, setName] = useState("");
  async function toggle(g: CameraGroup, on: boolean) {
    const items = g.cameras.filter((c) => key(c) !== key(cam)).map((c) => ({ camera_kind: c.kind, camera_id: c.id }));
    if (on) items.push({ camera_kind: cam.kind, camera_id: cam.id });
    await api(`/api/groups/${g.id}/items`, { method: "PUT", json: { items } });
    reload();
  }
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="row"><h3 className="grow" style={{ margin: 0 }}>Groups for “{cam.name}”</h3><button className="btn icon sm" onClick={onClose} aria-label="Close">✕</button></div>
        {groups.map((g) => (
          <label key={g.id} className="row card">
            <input type="checkbox" checked={g.cameras.some((c) => key(c) === key(cam))} onChange={(e) => toggle(g, e.target.checked)} />
            <span className="grow">{g.name}</span><span className="small muted">{g.cameras.length}</span>
          </label>
        ))}
        <form className="row" onSubmit={async (e) => {
          e.preventDefault();
          const g = await api<CameraGroup>("/api/groups", { method: "POST", json: { name } });
          await api(`/api/groups/${g.id}/items`, { method: "PUT", json: { items: [{ camera_kind: cam.kind, camera_id: cam.id }] } });
          setName("");
          reload();
        }}>
          <input className="input grow" placeholder="New group name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} />
          <button className="btn primary">Create</button>
        </form>
      </div>
    </div>
  );
}

export default function Dashboard() {
  const router = useRouter();
  const isDesktop = useIsDesktop();
  const [user, setUser] = useState<User | null>(null);
  const [view, setView] = useState<View>("cameras");
  const [cameras, setCameras] = useState<PrivateCamera[]>([]);
  const [favorites, setFavorites] = useState<AnyCamera[]>([]);
  const [groups, setGroups] = useState<CameraGroup[]>([]);
  const [gateways, setGateways] = useState<Gateway[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [sites, setSites] = useState<Site[]>([]);
  const [recent, setRecent] = useState<{ viewed_at: string; camera: AnyCamera }[]>([]);
  const [tiles, setTiles] = useState<AnyCamera[]>([]);
  const [layout, setLayout] = useState<Layout>("l1");
  const [diagTarget, setDiagTarget] = useState<AnyCamera | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [groupFor, setGroupFor] = useState<AnyCamera | null>(null);
  // public discovery
  const [center, setCenter] = useState<[number, number]>(DEFAULT_CENTER);
  const [mapCenter, setMapCenter] = useState<[number, number]>(DEFAULT_CENTER);
  const [zoomKey, setZoomKey] = useState(0);
  const [radius, setRadius] = useState(15);
  const [typeFilter, setTypeFilter] = useState("");
  const [userLoc, setUserLoc] = useState<[number, number] | null>(null);
  const [geoMsg, setGeoMsg] = useState<string | null>(null);
  const [publicCams, setPublicCams] = useState<PublicCamera[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    api<User>("/api/auth/me").then(setUser).catch(() => router.replace("/login"));
  }, [router]);

  const loadStatus = useCallback(async () => {
    const [c, g] = await Promise.all([api<PrivateCamera[]>("/api/cameras"), api<Gateway[]>("/api/gateways")]);
    setCameras(c);
    setGateways(g);
    // keep open tiles' metadata (status, gateway) fresh
    const byKey = new Map(c.map((x) => [key(x), x]));
    setTiles((t) => t.map((x) => byKey.get(key(x)) ?? x));
  }, []);

  const loadAll = useCallback(async () => {
    await Promise.all([
      loadStatus(),
      api<AnyCamera[]>("/api/favorites").then(setFavorites),
      api<CameraGroup[]>("/api/groups").then(setGroups),
      api<Device[]>("/api/devices").then(setDevices),
      api<Site[]>("/api/sites").then(setSites),
      api<{ viewed_at: string; camera: AnyCamera }[]>("/api/history").then(setRecent),
    ]).catch(() => {});
  }, [loadStatus]);

  useEffect(() => {
    if (!user) return;
    loadAll();
    const t = setInterval(() => document.visibilityState === "visible" && loadStatus().catch(() => {}), POLL_MS);
    return () => clearInterval(t);
  }, [user, loadAll, loadStatus]);

  const loadNearby = useCallback(async (c: [number, number], r: number, t: string) => {
    const q = new URLSearchParams({ lat: String(c[0]), lon: String(c[1]), radius_km: String(r) });
    if (t) q.set("camera_type", t);
    try {
      setPublicCams(await api<PublicCamera[]>(`/api/public-cameras/nearby?${q}`));
    } catch (e) {
      setMsg((e as Error).message);
    }
  }, []);

  useEffect(() => {
    if (user && view === "discover") loadNearby(center, radius, typeFilter);
  }, [user, view, center, radius, typeFilter, loadNearby]);

  const favSet = useMemo(() => new Set(favorites.map(key)), [favorites]);
  const openIds = useMemo(() => new Set(tiles.map((t) => t.id)), [tiles]);

  function open(cam: AnyCamera) {
    setTiles((t) => {
      if (t.some((x) => key(x) === key(cam))) return t;
      const next = [...t, cam];
      const need: Layout = next.length <= 1 ? "l1" : next.length <= 4 ? "l4" : "l9";
      setLayout((l) => (MAX_TILES[l] >= next.length ? l : need));
      return next.slice(-9);
    });
    if (!isDesktop) setView("live");
  }

  function openMany(cams: AnyCamera[]) {
    const list = cams.slice(0, 9);
    setTiles(list);
    setLayout(list.length <= 1 ? "l1" : list.length <= 4 ? "l4" : "l9");
    if (!isDesktop) setView("live");
  }

  async function toggleFavorite(cam: AnyCamera) {
    const on = favSet.has(key(cam));
    await api(`/api/favorites/${cam.kind}/${cam.id}`, { method: on ? "DELETE" : "PUT" });
    setFavorites(await api<AnyCamera[]>("/api/favorites"));
    if (cam.kind === "public") setPublicCams((l) => l.map((c) => (c.id === cam.id ? { ...c, saved: !on } : c)));
  }

  function diagnose(cam: AnyCamera) {
    setDiagTarget(cam);
    setView("diagnostics");
  }

  function locate() {
    if (!("geolocation" in navigator)) return setGeoMsg("Geolocation is not supported by this browser.");
    setGeoMsg("Waiting for permission…");
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const c: [number, number] = [pos.coords.latitude, pos.coords.longitude];
        setUserLoc(c);
        setCenter(c);
        setZoomKey((k) => k + 1);
        setGeoMsg(`Location accuracy ±${Math.round(pos.coords.accuracy)} m. Used only for public camera search – never to decide how your own cameras connect.`);
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
      const errs = Object.entries(res.errors).map(([k, v]) => `${k} failed (${v})`).join("; ");
      setMsg(`Directory scan complete${errs ? `. ${errs}` : ""}`);
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    await api("/api/auth/logout", { method: "POST" }).catch(() => {});
    router.replace("/login");
  }

  const diagCandidates = useMemo(() => {
    const m = new Map<string, AnyCamera>();
    [...tiles, ...cameras.filter((c) => c.is_owner), ...favorites, ...recent.map((r) => r.camera)]
      .filter((c) => c.kind === "public" || c.is_owner)
      .forEach((c) => m.set(key(c), c));
    return [...m.values()];
  }, [tiles, cameras, favorites, recent]);

  if (!user) return <div className="auth-wrap muted">Loading…</div>;

  const sideView: View = view === "live" ? "cameras" : view;
  const offlineGw = gateways.filter((g) => !g.online && g.status !== "pending").length;

  const map = (
    <div className="map-wrap">
      <MapView
        center={center}
        zoomKey={zoomKey}
        radiusKm={radius}
        userLocation={userLoc}
        publicCameras={publicCams}
        privateCameras={cameras}
        selectedId={null}
        onSelect={open}
        onCenterChange={setMapCenter}
      />
      <div className="legend">
        <span className="row"><span className="dot" style={{ background: "#38bdf8" }} /> Public</span>
        <span className="row"><span className="dot" style={{ background: "#f59e0b" }} /> Yours</span>
      </div>
    </div>
  );

  const discoverPanel = (
    <>
      {!isDesktop && map}
      <div className="notice small">Public feeds published openly by their operators (traffic agencies, webcams). Your own cameras are under Cameras.</div>
      <div className="row">
        <button className="btn primary grow" onClick={locate}>📍 Near me</button>
        <button className="btn grow" onClick={() => setCenter(mapCenter)}>Search this area</button>
      </div>
      {geoMsg && <div className="small muted">{geoMsg}</div>}
      <label className="field">
        Radius: {radius} km
        <input type="range" min={1} max={200} value={radius} onChange={(e) => setRadius(Number(e.target.value))} onMouseUp={() => setZoomKey((k) => k + 1)} onTouchEnd={() => setZoomKey((k) => k + 1)} />
      </label>
      <div className="row">
        <select className="select grow" value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} aria-label="Camera type">
          <option value="">All public types</option>
          <option value="traffic">Traffic cameras</option>
          <option value="webcam">Webcams</option>
          <option value="public_ip">Public IP cameras</option>
        </select>
        <button className="btn" onClick={discover} disabled={busy}>{busy ? "Scanning…" : "Scan directories"}</button>
      </div>
      <div className="small muted">{publicCams.length} public cameras within {radius} km.</div>
      {msg && <div className="notice small">{msg}</div>}
      <div className="cam-list">
        {publicCams.map((c) => (
          <CameraRow key={c.id} cam={c} open={openIds.has(c.id)} favorite={favSet.has(key(c))} onOpen={() => open(c)} onFavorite={() => toggleFavorite(c)} />
        ))}
      </div>
    </>
  );

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand"><div className="brand-logo">◉</div> CameraScope</div>
        <span className="legend-badges"><span className="badge private">Your cameras</span><span className="badge public">Public feeds</span></span>
        <div className="spacer" />
        {offlineGw > 0 && <button className="badge neutral" style={{ color: "#fca5a5", cursor: "pointer" }} onClick={() => setView("manage")}>⚠ {offlineGw} gateway offline</button>}
        <span className="muted small ellipsis" style={{ maxWidth: 140 }}>{user.display_name}</span>
        <button className="btn sm" onClick={logout}>Sign out</button>
      </header>

      <div className="main">
        <aside className={`sidebar ${view === "live" ? "hidden-mobile" : ""}`}>
          <nav className="tabs">
            {([["cameras", `Cameras (${cameras.length})`], ["discover", "Discover"], ["manage", "Manage"], ["diagnostics", "Diagnostics"]] as [View, string][]).map(([id, label]) => (
              <button key={id} className={`tab ${sideView === id ? "active" : ""}`} onClick={() => setView(id)}>{label}</button>
            ))}
          </nav>
          <div className="panel-body">
            {sideView === "cameras" && (
              <CamerasPanel
                cameras={cameras}
                favorites={favorites}
                groups={groups}
                recent={recent}
                openIds={openIds}
                isFavorite={(c) => favSet.has(key(c))}
                onOpen={open}
                onOpenMany={openMany}
                onFavorite={toggleFavorite}
                onAddToGroup={setGroupFor}
                onAddCamera={() => setShowAdd(true)}
              />
            )}
            {sideView === "discover" && discoverPanel}
            {sideView === "manage" && (
              <ManagePanel gateways={gateways} devices={devices} sites={sites} groups={groups} cameras={cameras} reload={loadAll} onAddCamera={() => setShowAdd(true)} onDiagnose={diagnose} />
            )}
            {sideView === "diagnostics" && (
              <DiagnosticsPanel target={diagTarget} candidates={diagCandidates} onPick={setDiagTarget} onRan={loadStatus} />
            )}
          </div>
        </aside>

        <section className={`content ${view !== "live" ? "hidden-mobile" : ""}`}>
          {isDesktop && sideView === "discover" && map}
          <div className="viewer">
            <div className="viewer-bar">
              <strong className="grow">Live</strong>
              {(["l1", "l4", "l9"] as Layout[]).map((l) => (
                <button key={l} className={`btn sm ${layout === l ? "primary" : ""}`} aria-label={`${MAX_TILES[l]} feeds`}
                  onClick={() => { setLayout(l); setTiles((t) => t.slice(0, MAX_TILES[l])); }}>
                  {l === "l1" ? "1" : l === "l4" ? "4" : "9"}
                </button>
              ))}
              <button className="btn sm" onClick={() => setTiles([])} disabled={!tiles.length}>Clear</button>
            </div>
            <div className={`grid ${layout}`}>
              {tiles.length === 0 && (
                <div className="empty" style={{ gridColumn: "1 / -1" }}>
                  <div>
                    <div style={{ marginBottom: 8 }}>Choose cameras to watch.</div>
                    {!isDesktop && <button className="btn primary" onClick={() => setView("cameras")}>Open camera list</button>}
                  </div>
                </div>
              )}
              {tiles.map((c) => (
                <CameraTile
                  key={key(c)}
                  camera={c}
                  onClose={() => setTiles((t) => t.filter((x) => key(x) !== key(c)))}
                  onDiagnose={diagnose}
                  onExpand={(cam) => { setTiles([cam]); setLayout("l1"); }}
                />
              ))}
            </div>
          </div>
        </section>
      </div>

      <nav className="bottom-nav" aria-label="Main">
        {([["live", "▶", `Live`], ["cameras", "◉", "Cameras"], ["discover", "🗺", "Discover"], ["manage", "⚙", "Manage"]] as [View, string, string][]).map(([id, ic, label]) => (
          <button key={id} className={view === id || (id === "manage" && view === "diagnostics") ? "active" : ""} onClick={() => setView(id)}>
            <span className="ic">{ic}</span>
            <span>{label}{id === "live" && tiles.length > 0 && <span className="count">{tiles.length}</span>}{id === "manage" && offlineGw > 0 && " ⚠"}</span>
          </button>
        ))}
      </nav>

      {showAdd && (
        <AddCameraDialog
          defaultLocation={userLoc}
          gateways={gateways}
          sites={sites}
          onClose={() => setShowAdd(false)}
          onCreated={(cam) => { setShowAdd(false); loadAll(); open(cam); }}
        />
      )}
      {groupFor && <GroupPicker cam={groupFor} groups={groups} onClose={() => setGroupFor(null)} reload={() => api<CameraGroup[]>("/api/groups").then(setGroups)} />}
    </div>
  );
}
