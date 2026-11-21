"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { AnyCamera, Playback } from "@/lib/types";
import Player, { PlayerHandle, PlayerStatus } from "./Player";

interface Props {
  camera: AnyCamera;
  onClose: () => void;
  onDiagnose: (cam: AnyCamera) => void;
  onExpand?: (cam: AnyCamera) => void;
}

function download(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// Errors where retrying cannot help until the user acts.
const PERMANENT = new Set([400, 403, 404, 409, 423]);
const STALL_MS = 12000;

export default function CameraTile({ camera, onClose, onDiagnose, onExpand }: Props) {
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
  const [attempt, setAttempt] = useState(0);
  const [countdown, setCountdown] = useState<number | null>(null);
  const [permanent, setPermanent] = useState(false);
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
    setPermanent(false);
    const url = isPublic ? `/api/public-cameras/${camera.id}/playback` : `/api/cameras/${camera.id}/playback?quality=${quality}`;
    api<Playback>(url)
      .then((pb) => !off && setPlayback(pb))
      .catch((e) => {
        if (off) return;
        setStatus("error");
        setDetail((e as Error).message);
        if (e instanceof ApiError && PERMANENT.has(e.status)) setPermanent(true);
      });
    return () => {
      off = true;
    };
  }, [camera.id, isPublic, quality, retry]);

  const onStatus = useCallback((s: PlayerStatus, d?: string) => {
    setStatus(s);
    setDetail(d || null);
    if (s === "playing") {
      setAttempt(0);
      setCountdown(null);
    }
  }, []);

  // Automatic reconnection with exponential backoff (2s, 4s, 8s ... 30s).
  useEffect(() => {
    const live = playback && playback.mode !== "webpage";
    let timer: ReturnType<typeof setTimeout> | undefined;
    if (status === "error" && !permanent && (live || !playback)) {
      const delay = Math.min(30, 2 ** Math.min(attempt + 1, 5));
      setCountdown(delay);
      const started = Date.now();
      const tick = setInterval(() => setCountdown(Math.max(0, delay - Math.round((Date.now() - started) / 1000))), 1000);
      timer = setTimeout(() => {
        clearInterval(tick);
        setAttempt((a) => a + 1);
        setRetry((r) => r + 1);
      }, delay * 1000);
      return () => {
        clearTimeout(timer);
        clearInterval(tick);
      };
    }
    if (status === "stalled" && live) {
      timer = setTimeout(() => {
        setStatus("error");
        setDetail("Stream stalled");
      }, STALL_MS);
      return () => clearTimeout(timer);
    }
    if (status === "connecting" && live) {
      timer = setTimeout(() => {
        setStatus("error");
        setDetail("No video received");
      }, 25000);
      return () => clearTimeout(timer);
    }
  }, [status, permanent, playback, attempt]);

  // Resume immediately when the tab/app comes back to the foreground (mobile).
  useEffect(() => {
    const onVis = () => {
      if (document.visibilityState === "visible" && status === "error" && !permanent) setRetry((r) => r + 1);
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, [status, permanent]);

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
    const el = tileRef.current as (HTMLDivElement & { webkitRequestFullscreen?: () => void }) | null;
    if (!el) return;
    const doc = document as Document & { webkitFullscreenElement?: Element; webkitExitFullscreen?: () => void };
    if (document.fullscreenElement || doc.webkitFullscreenElement) {
      (document.exitFullscreen || doc.webkitExitFullscreen)?.call(document);
    } else if (el.requestFullscreen) {
      el.requestFullscreen().catch(() => playerRef.current?.nativeFullscreen());
    } else if (el.webkitRequestFullscreen) {
      el.webkitRequestFullscreen();
    } else {
      playerRef.current?.nativeFullscreen(); // iPhone Safari
    }
  }

  const qualityOptions = playback?.qualities ?? [];
  const showHlsLevels = levels.length > 1;
  const priv = camera.kind === "private" ? camera : null;
  const via = priv ? (priv.gateway ? `via ${priv.gateway.name}` : "direct") : (camera.kind === "public" ? camera.source_name : "");

  return (
    <div ref={tileRef} className={`tile ${camera.kind}`}>
      <div className="tile-head">
        <span className={`dot s-${status}`} title={status} />
        <span className="name" title={camera.name} onDoubleClick={() => onExpand?.(camera)}>{camera.name}</span>
        <span className={`badge ${camera.kind}`}>{isPublic ? "Public" : priv?.access === "shared" ? "Shared" : "Private"}</span>
        <button className="btn icon sm" title="Close" onClick={onClose} aria-label="Close">✕</button>
      </div>
      <div className="tile-body" onDoubleClick={fullscreen}>
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
        {status === "connecting" && playback?.mode !== "webpage" && (
          <div className="tile-overlay">{attempt > 0 ? `Reconnecting… (attempt ${attempt + 1})` : "Connecting…"}</div>
        )}
        {status === "error" && (
          <div className="tile-overlay err">
            <div>
              <div style={{ marginBottom: 8 }}>{detail || "Feed unavailable"}</div>
              {!permanent && countdown !== null && <div className="small muted" style={{ marginBottom: 8 }}>Reconnecting automatically in {countdown}s</div>}
              <div className="row" style={{ justifyContent: "center" }}>
                <button className="btn sm" onClick={() => { setAttempt(0); setRetry((n) => n + 1); }}>Retry now</button>
                {(isPublic || priv?.is_owner) && <button className="btn sm" onClick={() => onDiagnose(camera)}>Diagnose</button>}
              </div>
            </div>
          </div>
        )}
      </div>
      <div className="tile-foot">
        <span className="grow ellipsis">
          {status === "playing" ? "● Live" : status}
          {transport && ` · ${transport}`}
          {via && ` · ${via}`}
          {playback?.attribution && ` · ${playback.attribution}`}
        </span>
        {!isPublic && qualityOptions.length > 1 && (
          <select className="select mini" value={quality} onChange={(e) => setQuality(e.target.value)} title="Stream quality" aria-label="Stream quality">
            {qualityOptions.map((q) => (
              <option key={q.id} value={q.id}>{q.id === "high" ? "HD" : "SD"}</option>
            ))}
          </select>
        )}
        {showHlsLevels && (
          <select className="select mini" value={hlsLevel} onChange={(e) => setHlsLevel(Number(e.target.value))} title="Stream quality" aria-label="Stream quality">
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
          title={playback?.snapshot_allowed ? "Save snapshot" : "Snapshots not permitted for this feed"}
          aria-label="Snapshot"
        >
          📷
        </button>
        {(isPublic || priv?.is_owner) && (
          <button className="btn icon sm" onClick={() => onDiagnose(camera)} title="Connection diagnostics" aria-label="Diagnostics">🩺</button>
        )}
        <button className="btn icon sm" onClick={fullscreen} title="Full screen" aria-label="Full screen">⛶</button>
      </div>
    </div>
  );
}
