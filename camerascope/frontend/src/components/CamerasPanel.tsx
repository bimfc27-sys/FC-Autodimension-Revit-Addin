"use client";

import { useMemo, useState } from "react";
import { FREE } from "@/lib/edition";
import type { AnyCamera, CameraGroup, PrivateCamera } from "@/lib/types";

export const STATUS_TEXT: Record<string, string> = {
  online: "Online",
  offline: "Offline",
  auth_failed: "Login rejected",
  gateway_offline: "Gateway offline",
  not_internet_reachable: "Not reachable from the internet",
  unknown: "Checking…",
};

/** Free Edition: whether this camera can show anything, and why not. */
export function VideoChip({ cam }: { cam: PrivateCamera }) {
  if (!cam.video) return null;
  if (cam.video.available) {
    return <span className="badge public" title={`Verified ${cam.video.verified_at ? new Date(cam.video.verified_at).toLocaleString() : ""}`}>📷 Snapshot view</span>;
  }
  return <span className="badge neutral" title={cam.video.reason ?? ""}>No video</span>;
}

export function ConnectionLabel({ cam }: { cam: PrivateCamera }) {
  if (FREE) return <span className="conn" title="Reached by Cloudflare over the internet">🌐 Internet</span>;
  if (cam.gateway) {
    return (
      <span className={`conn ${cam.gateway.online ? "" : "gw-off"}`} title="Local Camera Gateway this camera is reached through">
        ⇄ {cam.gateway.name}
        {!cam.gateway.online && " (offline)"}
      </span>
    );
  }
  return <span className="conn" title="Reached directly by the cloud (public address or VPN route)">☁ Direct / VPN</span>;
}

interface RowProps {
  cam: AnyCamera;
  open: boolean;
  favorite: boolean;
  onOpen: () => void;
  onFavorite: () => void;
  onGroup?: () => void;
}

export function CameraRow({ cam, open, favorite, onOpen, onFavorite, onGroup }: RowProps) {
  const priv = cam.kind === "private" ? cam : null;
  return (
    <div className={`cam-item ${open ? "open" : ""}`} onClick={onOpen} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onOpen()}>
      <div className="row">
        <span className={`dot s-${cam.status}`} />
        <span className="title grow">{cam.name}</span>
        <button
          className="btn icon sm"
          onClick={(e) => { e.stopPropagation(); onFavorite(); }}
          aria-label={favorite ? "Remove from favorites" : "Add to favorites"}
          title={favorite ? "Remove from favorites" : "Add to favorites"}
        >
          {favorite ? "★" : "☆"}
        </button>
        {onGroup && (
          <button className="btn icon sm" onClick={(e) => { e.stopPropagation(); onGroup(); }} aria-label="Add to group" title="Add to group">＋</button>
        )}
      </div>
      <div className="meta">
        <span className={`status-label s-${cam.status}`}>{STATUS_TEXT[cam.status] ?? cam.status}</span>
        {priv ? (
          <>
            <span>·</span>
            <ConnectionLabel cam={priv} />
            {priv.device && <span>· {priv.device.name}{priv.channel ? ` CH${priv.channel}` : ""}</span>}
            {priv.access === "shared" && <span className="badge private">Shared</span>}
            <VideoChip cam={priv} />
          </>
        ) : (
          <>
            <span>· {cam.kind === "public" ? cam.source_name : ""}</span>
            <span className="badge public">Public</span>
          </>
        )}
      </div>
      {priv?.video && !priv.video.available && priv.video.reason && <div className="small muted">{priv.video.reason}</div>}
    </div>
  );
}

interface Props {
  cameras: PrivateCamera[];
  favorites: AnyCamera[];
  groups: CameraGroup[];
  recent: { viewed_at: string; camera: AnyCamera }[];
  openIds: Set<string>;
  isFavorite: (cam: AnyCamera) => boolean;
  onOpen: (cam: AnyCamera) => void;
  onOpenMany: (cams: AnyCamera[]) => void;
  onFavorite: (cam: AnyCamera) => void;
  onAddToGroup: (cam: AnyCamera) => void;
  onAddCamera: () => void;
}

export default function CamerasPanel(p: Props) {
  const [q, setQ] = useState("");
  const match = (c: AnyCamera) => !q || c.name.toLowerCase().includes(q.toLowerCase());

  const { mine, shared } = useMemo(() => {
    const mine = new Map<string, { label: string; online: boolean | null; cams: PrivateCamera[] }>();
    const shared = new Map<string, { label: string; cams: PrivateCamera[] }>();
    for (const c of p.cameras) {
      if (c.access === "shared") {
        const k = c.site?.id ?? "-";
        if (!shared.has(k)) shared.set(k, { label: c.site?.name ?? "Shared", cams: [] });
        shared.get(k)!.cams.push(c);
      } else {
        const k = c.gateway?.id ?? "direct";
        if (!mine.has(k)) mine.set(k, { label: c.gateway ? `Gateway: ${c.gateway.name}` : FREE ? "My cameras" : "Direct / VPN", online: c.gateway ? c.gateway.online : null, cams: [] });
        mine.get(k)!.cams.push(c);
      }
    }
    return { mine: [...mine.values()], shared: [...shared.values()] };
  }, [p.cameras]);

  const online = p.cameras.filter((c) => c.status === "online").length;
  const row = (c: AnyCamera, onGroup = true) => (
    <CameraRow
      key={`${c.kind}:${c.id}`}
      cam={c}
      open={p.openIds.has(c.id)}
      favorite={p.isFavorite(c)}
      onOpen={() => p.onOpen(c)}
      onFavorite={() => p.onFavorite(c)}
      onGroup={onGroup ? () => p.onAddToGroup(c) : undefined}
    />
  );

  return (
    <>
      <div className="row">
        <input className="input grow" placeholder="Search cameras" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search cameras" />
        <button className="btn primary" onClick={p.onAddCamera}>+ Add</button>
      </div>
      <div className="small muted">{p.cameras.length} cameras · {online} online</div>

      {p.favorites.filter(match).length > 0 && (
        <>
          <div className="section-title">★ Favorites<span className="line" />
            <button className="btn sm" onClick={() => p.onOpenMany(p.favorites)}>▶ Play all</button>
          </div>
          <div className="cam-list">{p.favorites.filter(match).map((c) => row(c))}</div>
        </>
      )}

      {p.groups.length > 0 && (
        <>
          <div className="section-title">Groups<span className="line" /></div>
          <div className="cam-list">
            {p.groups.map((g) => (
              <div key={g.id} className="card">
                <div className="row">
                  <strong className="grow ellipsis">{g.name}</strong>
                  <span className="small muted">{g.cameras.length}</span>
                  <button className="btn sm" disabled={!g.cameras.length} onClick={() => p.onOpenMany(g.cameras)}>▶ Play</button>
                </div>
                <div className="chips">
                  {g.cameras.map((c) => (
                    <button key={c.id} className="btn sm" onClick={() => p.onOpen(c)}>
                      <span className={`dot s-${c.status}`} /> {c.name}
                    </button>
                  ))}
                  {!g.cameras.length && <span className="small muted">Empty – add cameras with ＋</span>}
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {mine.map((grp) => (
        <div key={grp.label} className="cam-list">
          <div className="section-title">
            {grp.online === false ? "⚠ " : ""}{grp.label}<span className="line" />
            <button className="btn sm" onClick={() => p.onOpenMany(grp.cams)}>▶ Play all</button>
          </div>
          {grp.online === false && <div className="small" style={{ color: "#fca5a5" }}>This gateway is offline. Its cameras come back automatically when it reconnects.</div>}
          {grp.cams.filter(match).map((c) => row(c))}
        </div>
      ))}

      {shared.length > 0 && (
        <>
          <div className="section-title">🏢 Building cameras (shared with you)<span className="line" /></div>
          {shared.map((grp) => (
            <div key={grp.label} className="cam-list">
              <div className="row small"><strong className="grow">{grp.label}</strong>
                <button className="btn sm" onClick={() => p.onOpenMany(grp.cams)}>▶ Play all</button>
              </div>
              {grp.cams.filter(match).map((c) => row(c))}
            </div>
          ))}
        </>
      )}

      {p.cameras.length === 0 && (
        <div className="empty">
          <div>
            <div style={{ marginBottom: 8 }}>No cameras yet.</div>
            <div className="small">Add a camera, or set up a Local Camera Gateway under Manage for cameras on another network or behind an NVR.</div>
          </div>
        </div>
      )}

      {p.recent.length > 0 && (
        <>
          <div className="section-title">Recently viewed<span className="line" /></div>
          <div className="cam-list">{p.recent.slice(0, 8).filter((r) => match(r.camera)).map((r) => row(r.camera, false))}</div>
        </>
      )}
    </>
  );
}
