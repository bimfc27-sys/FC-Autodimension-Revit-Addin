"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { AnyCamera, DiagnosticsRun } from "@/lib/types";

interface Props {
  target: AnyCamera | null;
  candidates: AnyCamera[];
  onPick: (cam: AnyCamera) => void;
  onRan: () => void;
}

function Steps({ run }: { run: DiagnosticsRun }) {
  return (
    <div className="steps">
      {run.steps.map((s, i) => (
        <div className="step" key={i}>
          <span className={`ic ${s.ok ? "ok" : "bad"}`}>{s.ok ? "✔" : "✖"}</span>
          <div>
            <div style={{ fontWeight: 600 }}>{s.name}</div>
            <div className="small muted" style={{ wordBreak: "break-word" }}>{s.detail}</div>
          </div>
          <span className="small muted">{s.duration_ms} ms</span>
        </div>
      ))}
    </div>
  );
}

export default function DiagnosticsPanel({ target, candidates, onPick, onRan }: Props) {
  const [run, setRun] = useState<DiagnosticsRun | null>(null);
  const [history, setHistory] = useState<DiagnosticsRun[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadHistory = () => api<DiagnosticsRun[]>("/api/diagnostics").then(setHistory).catch(() => {});
  useEffect(() => {
    loadHistory();
  }, []);
  useEffect(() => {
    setRun(null);
    setError(null);
  }, [target?.id]);

  async function go() {
    if (!target) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api<DiagnosticsRun>("/api/diagnostics", { method: "POST", json: { camera_kind: target.kind, camera_id: target.id } });
      setRun(r);
      loadHistory();
      onRan();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const nameOf = (kind: string, id: string) => candidates.find((c) => c.kind === kind && c.id === id)?.name ?? id.slice(0, 8);

  return (
    <>
      <label className="field">
        Camera
        <select
          className="select"
          value={target ? `${target.kind}:${target.id}` : ""}
          onChange={(e) => {
            const [k, id] = e.target.value.split(":");
            const c = candidates.find((x) => x.kind === k && x.id === id);
            if (c) onPick(c);
          }}
        >
          <option value="" disabled>Select a camera…</option>
          {candidates.map((c) => (
            <option key={`${c.kind}:${c.id}`} value={`${c.kind}:${c.id}`}>
              [{c.kind === "public" ? "Public" : "Private"}] {c.name}
            </option>
          ))}
        </select>
      </label>
      <button className="btn primary" disabled={!target || busy} onClick={go}>{busy ? "Running checks…" : "Run diagnostics"}</button>
      <div className="small muted">
        Checks DNS and network policy, TCP reachability, the protocol handshake (RTSP DESCRIBE, ONVIF, HLS playlist…) and the
        streaming gateway. Each run authenticates at most once with the stored credentials.
      </div>
      {error && <div className="error">{error}</div>}
      {run && (
        <>
          <div className="row">
            <strong className="grow">Result</strong>
            <span className={`badge ${run.ok ? "public" : "neutral"}`} style={run.ok ? {} : { color: "#fca5a5" }}>{run.ok ? "Healthy" : "Problem found"}</span>
          </div>
          <Steps run={run} />
        </>
      )}
      <strong style={{ marginTop: 8 }}>Recent runs</strong>
      <div className="cam-list">
        {history.length === 0 && <div className="muted small">No diagnostics yet.</div>}
        {history.map((h) => (
          <details key={h.id} className="cam-item" style={{ cursor: "default" }}>
            <summary className="row" style={{ cursor: "pointer" }}>
              <span className={`dot ${h.ok ? "s-online" : "s-error"}`} />
              <span className="grow title">{nameOf(h.camera_kind, h.camera_id)}</span>
              <span className={`badge ${h.camera_kind}`}>{h.camera_kind}</span>
              <span className="small muted">{h.created_at ? new Date(h.created_at).toLocaleTimeString() : ""}</span>
            </summary>
            <div style={{ marginTop: 6 }}><Steps run={h} /></div>
          </details>
        ))}
      </div>
    </>
  );
}
