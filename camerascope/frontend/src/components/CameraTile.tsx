"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { AnyCamera, Playback } from "@/lib/types";
import Player, { PlayerHandle, PlayerStatus } from "./Player";

interface Props {
  camera: AnyCamera;
  onClose: () => void;
  onDiagnose: (cam: AnyCamera) => void;
}

function download(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function CameraTile({ camera, onClose, onDiagnose }: Props) {
  const tileRef = useRef<HTMLDivElement>(null);
  const playerRef = useRef<PlayerHandle>(null);
  const [quality, setQuality] = useState("high");
  const [playback, setPlayback] = useState<Playback | null>(null);
  const [status, setStatus] = useState<PlayerStatus>("connecting");
  const [detail, setDetail] = useState<string | null>(null);
  const [levels, setLevels] = useState<{ index: number; label: string }[]>([]);
  const [hlsLevel, setHlsLevel] = useState(-1);
  const [transport, setTransport] = useState<string>("");
  const [retry, setRetry] = useState(0);
  const isPublic = camera.kind === "public";

  useEffect(() => {
    api("/api/history", { method: "POST", json: { camera_kind: camera.kind, camera_id: camera.id } }).catch(() => {});
  }, [camera.kind, camera.id]);

  useEffect(() => {
    let off = false;
    setPlayback(null);
    setStatus("connecting");
    setDetail(null);
    setLevels([]);
    const url = isPublic ? `/api/public-cameras/${camera.id}/playback` : `/api/cameras/${camera.id}/playback?quality=${quality}`;
    api<Playback>(url)
      .then((pb) => !off && setPlayback(pb))
      .catch((e) => {
        if (off) return;
        setStatus("error");
        setDetail((e as Error).message);
      });
    return () => {
      off = true;
    };
  }, [camera.id, isPublic, quality, retry]);

  const onStatus = useCallback((s: PlayerStatus, d?: string) => {
    setStatus(s);
    setDetail(d || null);
  }, []);

  async function snapshot() {
    if (!playback?.snapshot_allowed) return;
    const name = `${camera.name.replace(/[^\w-]+/g, "_")}-${new Date().toISOString().replace(/[:.]/g, "-")}.jpg`;
    const blob = await playerRef.current?.capture();
    if (blob) return download(blob, name);
    if (playback.snapshot_url) {
      const res = await fetch(playback.snapshot_url, { credentials: "same-origin" });
      if (res.ok) return download(await res.blob(), name);
    }
    setDetail("Snapshot not available for this feed");
  }

  function fullscreen() {
    const el = tileRef.current;
    if (!el) return;
    if (document.fullscreenElement) document.exitFullscreen();
    else el.requestFullscreen?.();
  }

  const qualityOptions = playback?.qualities ?? [];
  const showHlsLevels = levels.length > 1;

  return (
    <div ref={tileRef} className={`tile ${camera.kind}`}>
      <div className="tile-head">
        <span className={`dot s-${status}`} title={status} />
        <span className="name" title={camera.name}>{camera.name}</span>
        <span className={`badge ${camera.kind}`}>{isPublic ? "Public" : "Private"}</span>
        <button className="btn icon sm" title="Close" onClick={onClose} aria-label="Close">✕</button>
      </div>
      <div className="tile-body">
        {playback && (
          <Player
            key={`${playback.whep_url}|${playback.hls_url}|${playback.image_url}|${retry}`}
            ref={playerRef}
            playback={playback}
            hlsLevel={hlsLevel}
            onStatus={onStatus}
            onLevels={setLevels}
            onTransport={setTransport}
          />
        )}
        {status === "connecting" && playback?.mode !== "webpage" && <div className="tile-overlay">Connecting…</div>}
        {status === "error" && (
          <div className="tile-overlay err">
            <div>
              <div style={{ marginBottom: 8 }}>{detail || "Feed unavailable"}</div>
              <div className="row" style={{ justifyContent: "center" }}>
                <button className="btn sm" onClick={() => setRetry((n) => n + 1)}>Retry</button>
                <button className="btn sm" onClick={() => onDiagnose(camera)}>Diagnose</button>
              </div>
            </div>
          </div>
        )}
      </div>
      <div className="tile-foot">
        <span className="grow" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {status === "playing" ? "Live" : status}
          {transport && ` · ${transport}`}
          {playback?.attribution && ` · ${playback.attribution}`}
        </span>
        {!isPublic && qualityOptions.length > 1 && (
          <select className="select" style={{ width: "auto", padding: "2px 6px" }} value={quality} onChange={(e) => setQuality(e.target.value)} title="Stream quality">
            {qualityOptions.map((q) => (
              <option key={q.id} value={q.id}>{q.label}</option>
            ))}
          </select>
        )}
        {showHlsLevels && (
          <select className="select" style={{ width: "auto", padding: "2px 6px" }} value={hlsLevel} onChange={(e) => setHlsLevel(Number(e.target.value))} title="Stream quality">
            <option value={-1}>Auto</option>
            {levels.map((l) => (
              <option key={l.index} value={l.index}>{l.label}</option>
            ))}
          </select>
        )}
        <button
          className="btn icon sm"
          onClick={snapshot}
          disabled={!playback?.snapshot_allowed || status !== "playing" || playback.mode === "webpage"}
          title={playback?.snapshot_allowed ? "Save snapshot" : "Snapshots not permitted by this feed's operator"}
        >
          📷
        </button>
        <button className="btn icon sm" onClick={() => onDiagnose(camera)} title="Connection diagnostics">🩺</button>
        <button className="btn icon sm" onClick={fullscreen} title="Full screen">⛶</button>
      </div>
    </div>
  );
}
