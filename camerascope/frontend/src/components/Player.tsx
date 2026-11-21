"use client";

import Hls from "hls.js";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import type { Playback } from "@/lib/types";

export type PlayerStatus = "connecting" | "playing" | "stalled" | "error";

export interface PlayerHandle {
  /** Capture the current frame as a JPEG blob (null if the browser forbids it). */
  capture(): Promise<Blob | null>;
}

interface Props {
  playback: Playback;
  hlsLevel: number; // -1 = auto
  onStatus: (s: PlayerStatus, detail?: string) => void;
  onLevels?: (levels: { index: number; label: string }[]) => void;
  onTransport?: (t: string) => void;
}

async function waitIce(pc: RTCPeerConnection, ms: number) {
  if (pc.iceGatheringState === "complete") return;
  await new Promise<void>((resolve) => {
    const t = setTimeout(resolve, ms);
    pc.addEventListener("icegatheringstatechange", () => {
      if (pc.iceGatheringState === "complete") {
        clearTimeout(t);
        resolve();
      }
    });
  });
}

/** WebRTC playback through the authenticated WHEP proxy. Returns a cleanup function. */
async function startWhep(url: string, video: HTMLVideoElement, onFail: (msg: string) => void): Promise<() => void> {
  // No third-party STUN: the gateway advertises its public address (MTX_WEBRTCADDITIONALHOSTS),
  // so the viewer's IP is never disclosed to an external service.
  const pc = new RTCPeerConnection({ iceServers: [] });
  pc.addTransceiver("video", { direction: "recvonly" });
  pc.addTransceiver("audio", { direction: "recvonly" });
  pc.ontrack = (ev) => {
    if (video.srcObject !== ev.streams[0]) video.srcObject = ev.streams[0];
  };
  pc.onconnectionstatechange = () => {
    if (pc.connectionState === "failed") onFail("WebRTC connection failed");
  };
  await pc.setLocalDescription(await pc.createOffer());
  await waitIce(pc, 1500);
  const res = await fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/sdp" },
    body: pc.localDescription!.sdp,
  });
  if (res.status !== 201) {
    pc.close();
    throw new Error(`WHEP ${res.status}`);
  }
  const location = res.headers.get("Location");
  await pc.setRemoteDescription({ type: "answer", sdp: await res.text() });
  return () => {
    pc.close();
    if (location) fetch(location, { method: "DELETE", credentials: "same-origin" }).catch(() => {});
  };
}

const Player = forwardRef<PlayerHandle, Props>(function Player({ playback, hlsLevel, onStatus, onLevels, onTransport }, ref) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const hlsRef = useRef<Hls | null>(null);
  const [tick, setTick] = useState(0);
  const statusRef = useRef(onStatus);
  statusRef.current = onStatus;

  useImperativeHandle(ref, () => ({
    async capture() {
      const v = videoRef.current;
      const img = imgRef.current;
      const src = v && v.videoWidth ? v : img && img.naturalWidth ? img : null;
      if (!src) return null;
      const w = src instanceof HTMLVideoElement ? src.videoWidth : src.naturalWidth;
      const h = src instanceof HTMLVideoElement ? src.videoHeight : src.naturalHeight;
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      canvas.getContext("2d")!.drawImage(src, 0, 0, w, h);
      try {
        return await new Promise<Blob | null>((r) => canvas.toBlob(r, "image/jpeg", 0.92));
      } catch {
        return null; // tainted canvas: the feed's origin does not allow frame capture
      }
    },
  }));

  // Video modes: gateway (WHEP -> HLS fallback) and HLS.
  useEffect(() => {
    const video = videoRef.current;
    if (!video || (playback.mode !== "gateway" && playback.mode !== "hls")) return;
    let cancelled = false;
    let cleanup: (() => void) | null = null;
    statusRef.current("connecting");

    const playHls = (url: string) => {
      onTransport?.("HLS");
      if (Hls.isSupported()) {
        const hls = new Hls({ lowLatencyMode: true, backBufferLength: 30 });
        hlsRef.current = hls;
        hls.loadSource(url);
        hls.attachMedia(video);
        hls.on(Hls.Events.MANIFEST_PARSED, (_e, data) => {
          onLevels?.(
            data.levels.map((l, i) => ({ index: i, label: l.height ? `${l.height}p` : `${Math.round((l.bitrate || 0) / 1000)} kbps` })),
          );
          video.play().catch(() => {});
        });
        hls.on(Hls.Events.ERROR, (_e, data) => {
          if (data.fatal) {
            if (data.type === Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError();
            else statusRef.current("error", `Stream error: ${data.details}`);
          }
        });
        cleanup = () => {
          hls.destroy();
          hlsRef.current = null;
        };
      } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
        video.src = url;
        video.play().catch(() => {});
        cleanup = () => {
          video.removeAttribute("src");
          video.load();
        };
      } else {
        statusRef.current("error", "This browser cannot play HLS");
      }
    };

    (async () => {
      if (playback.mode === "gateway" && playback.whep_url && typeof RTCPeerConnection !== "undefined") {
        try {
          let failed = false;
          const fallback = (msg: string) => {
            if (failed || cancelled) return;
            failed = true;
            cleanup?.();
            cleanup = null;
            video.srcObject = null;
            if (playback.hls_url) playHls(playback.hls_url);
            else statusRef.current("error", msg);
          };
          cleanup = await startWhep(playback.whep_url, video, fallback);
          if (cancelled) return cleanup();
          onTransport?.("WebRTC");
          video.play().catch(() => {});
          // If no frames arrive in time, fall back to HLS.
          setTimeout(() => {
            if (!cancelled && !failed && video.readyState < 2) fallback("WebRTC timeout");
          }, 8000);
          return;
        } catch {
          /* fall through to HLS */
        }
      }
      if (!cancelled && playback.hls_url) playHls(playback.hls_url);
    })();

    return () => {
      cancelled = true;
      cleanup?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playback.mode, playback.whep_url, playback.hls_url]);

  useEffect(() => {
    if (hlsRef.current) hlsRef.current.currentLevel = hlsLevel;
  }, [hlsLevel]);

  // Still-image refresh.
  useEffect(() => {
    if (playback.mode !== "image") return;
    statusRef.current("connecting");
    const t = setInterval(() => setTick((n) => n + 1), (playback.refresh_seconds || 5) * 1000);
    return () => clearInterval(t);
  }, [playback.mode, playback.refresh_seconds]);

  useEffect(() => {
    if (playback.mode === "webpage") statusRef.current("playing");
    if (playback.mode === "mjpeg") statusRef.current("connecting");
  }, [playback.mode]);

  if (playback.mode === "webpage") {
    return (
      <div className="tile-overlay" style={{ pointerEvents: "auto" }}>
        <div>
          <div style={{ marginBottom: 8 }}>This operator publishes the feed on its own website only.</div>
          {playback.page_url && (
            <a className="btn" href={playback.page_url} target="_blank" rel="noopener noreferrer">
              Open live view ↗
            </a>
          )}
        </div>
      </div>
    );
  }

  if (playback.mode === "image" || playback.mode === "mjpeg") {
    const src = playback.mode === "image" ? `${playback.image_url}?t=${tick}` : playback.image_url!;
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        ref={imgRef}
        src={src}
        alt="Camera feed"
        onLoad={() => statusRef.current("playing")}
        onError={() => statusRef.current("error", "Feed unavailable")}
      />
    );
  }

  return (
    <video
      ref={videoRef}
      muted
      playsInline
      autoPlay
      crossOrigin={playback.mode === "hls" ? "anonymous" : undefined}
      onPlaying={() => statusRef.current("playing")}
      onWaiting={() => statusRef.current("stalled")}
      onError={() => statusRef.current("error", "Playback error")}
    />
  );
});

export default Player;
