"use client";

import L from "leaflet";
import { useEffect } from "react";
import { Circle, CircleMarker, MapContainer, TileLayer, Tooltip, useMap, useMapEvents } from "react-leaflet";
import type { AnyCamera, PrivateCamera, PublicCamera } from "@/lib/types";

interface Props {
  center: [number, number];
  zoomKey: number; // bump to recenter
  radiusKm: number;
  userLocation: [number, number] | null;
  publicCameras: PublicCamera[];
  privateCameras: PrivateCamera[];
  selectedId: string | null;
  onSelect: (cam: AnyCamera) => void;
  onCenterChange: (c: [number, number]) => void;
}

function Recenter({ center, zoomKey, radiusKm }: { center: [number, number]; zoomKey: number; radiusKm: number }) {
  const map = useMap();
  useEffect(() => {
    map.invalidateSize();
    map.fitBounds(L.latLng(center).toBounds(radiusKm * 2000), { animate: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zoomKey]);
  return null;
}

function CenterTracker({ onCenterChange }: { onCenterChange: (c: [number, number]) => void }) {
  useMapEvents({
    moveend(e) {
      const c = e.target.getCenter();
      onCenterChange([c.lat, c.lng]);
    },
  });
  return null;
}

const statusColor = (s: string) => (s === "offline" || s === "auth_failed" ? "#ef4444" : undefined);

export default function MapView(p: Props) {
  return (
    <MapContainer center={p.center} zoom={11} scrollWheelZoom style={{ height: "100%" }} worldCopyJump>
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        maxZoom={19}
      />
      <Recenter center={p.center} zoomKey={p.zoomKey} radiusKm={p.radiusKm} />
      <CenterTracker onCenterChange={p.onCenterChange} />
      <Circle center={p.center} radius={p.radiusKm * 1000} pathOptions={{ color: "#4f8cff", weight: 1, fillOpacity: 0.04 }} />
      {p.userLocation && (
        <CircleMarker center={p.userLocation} radius={7} pathOptions={{ color: "#fff", fillColor: "#4f8cff", fillOpacity: 1, weight: 2 }}>
          <Tooltip>Your location</Tooltip>
        </CircleMarker>
      )}
      {p.publicCameras.map((c) => (
        <CircleMarker
          key={c.id}
          center={[c.latitude, c.longitude]}
          radius={p.selectedId === c.id ? 9 : 6}
          eventHandlers={{ click: () => p.onSelect(c) }}
          pathOptions={{ color: statusColor(c.status) || "#0ea5e9", fillColor: "#38bdf8", fillOpacity: 0.85, weight: p.selectedId === c.id ? 3 : 1 }}
        >
          <Tooltip>
            <strong>{c.name}</strong>
            <br />
            PUBLIC · {c.camera_type} · {c.source_name}
            <br />
            {c.stream_type} · {c.status}
          </Tooltip>
        </CircleMarker>
      ))}
      {p.privateCameras
        .filter((c) => c.latitude != null && c.longitude != null)
        .map((c) => (
          <CircleMarker
            key={c.id}
            center={[c.latitude!, c.longitude!]}
            radius={p.selectedId === c.id ? 10 : 7}
            eventHandlers={{ click: () => p.onSelect(c) }}
            pathOptions={{ color: statusColor(c.status) || "#d97706", fillColor: "#f59e0b", fillOpacity: 0.9, weight: p.selectedId === c.id ? 3 : 1 }}
          >
            <Tooltip>
              <strong>{c.name}</strong>
              <br />
              PRIVATE · {c.protocol.toUpperCase()} · {c.status}
            </Tooltip>
          </CircleMarker>
        ))}
    </MapContainer>
  );
}
