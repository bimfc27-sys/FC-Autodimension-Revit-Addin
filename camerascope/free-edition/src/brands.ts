// What the Free Edition can do per brand, using ONLY documented interfaces and
// no manufacturer developer accounts. "snapshot" = the camera's own documented
// HTTP JPEG snapshot endpoint, fetched server-side through the internet.
// Live RTSP video is never available here (see docs/CLOUD-EDITION-AUDIT.md).

export type VideoCapability = "snapshot" | "none";

export interface Brand {
  id: string;
  label: string;
  kinds: ("camera" | "nvr")[];
  defaultHttpPort: number;
  defaultRtspPort: number;
  /** documented snapshot path for a channel, or null */
  snapshotPath: ((ch: number) => string) | null;
  /** credentials go in the query string (Reolink's documented API) instead of HTTP auth */
  queryAuth?: (user: string, pass: string) => string;
  channelDiscovery: "hikvision" | "dahua" | null;
  video: VideoCapability;
  /** shown to the user: what works and why */
  notes: string;
  docs: string;
}

export const BRANDS: Record<string, Brand> = {
  hikvision: {
    id: "hikvision",
    label: "Hikvision (camera / NVR / DVR)",
    kinds: ["camera", "nvr"],
    defaultHttpPort: 80,
    defaultRtspPort: 554,
    snapshotPath: (ch) => `/ISAPI/Streaming/channels/${ch}01/picture`,
    channelDiscovery: "hikvision",
    video: "snapshot",
    notes: "Snapshot view through the documented ISAPI picture endpoint (digest auth). Requires the device's HTTP/HTTPS port to be reachable from the internet (router port-forward). No live RTSP video in the Free Edition.",
    docs: "Hikvision ISAPI",
  },
  dahua: {
    id: "dahua",
    label: "Dahua (camera / NVR / XVR)",
    kinds: ["camera", "nvr"],
    defaultHttpPort: 80,
    defaultRtspPort: 554,
    snapshotPath: (ch) => `/cgi-bin/snapshot.cgi?channel=${ch}`,
    channelDiscovery: "dahua",
    video: "snapshot",
    notes: "Snapshot view through the documented HTTP API snapshot.cgi (digest auth). Requires internet reachability (port-forward). No live RTSP video in the Free Edition.",
    docs: "Dahua HTTP API",
  },
  reolink: {
    id: "reolink",
    label: "Reolink (camera / NVR)",
    kinds: ["camera", "nvr"],
    defaultHttpPort: 443,
    defaultRtspPort: 554,
    snapshotPath: (ch) => `/cgi-bin/api.cgi?cmd=Snap&channel=${ch - 1}&rs=camerascope`,
    queryAuth: (u, p) => `&user=${encodeURIComponent(u)}&password=${encodeURIComponent(p)}`,
    channelDiscovery: null,
    video: "snapshot",
    notes: "Snapshot view through Reolink's documented HTTP API (cmd=Snap). That API puts credentials in the URL, so use HTTPS: the request is made server-side and the password never reaches your browser. Requires port-forward.",
    docs: "Reolink camera HTTP API",
  },
  uniview: {
    id: "uniview",
    label: "Uniview (camera / NVR)",
    kinds: ["camera", "nvr"],
    defaultHttpPort: 80,
    defaultRtspPort: 554,
    snapshotPath: null,
    channelDiscovery: null,
    video: "snapshot",
    notes: "No default snapshot path is built in: enter the snapshot path documented for your model (Uniview HTTP/LAPI or ONVIF snapshot URI). Video is enabled only if that path returns a JPEG during verification.",
    docs: "Uniview LAPI / ONVIF",
  },
  ezviz: {
    id: "ezviz",
    label: "EZVIZ",
    kinds: ["camera"],
    defaultHttpPort: 80,
    defaultRtspPort: 554,
    snapshotPath: null,
    channelDiscovery: null,
    video: "none",
    notes: "EZVIZ cloud video needs an EZVIZ developer account (excluded by your requirements) and EZVIZ cameras expose RTSP only on the LAN. Registration, metadata and reachability are supported; no video in the Free Edition.",
    docs: "EZVIZ Open Platform (developer account required)",
  },
  imou: {
    id: "imou",
    label: "Imou",
    kinds: ["camera"],
    defaultHttpPort: 80,
    defaultRtspPort: 554,
    snapshotPath: null,
    channelDiscovery: null,
    video: "none",
    notes: "Imou cloud video needs an Imou Open Platform developer account (excluded). Registration, metadata and reachability are supported; no video in the Free Edition. If your model documents an HTTP snapshot URL, choose “Other / ONVIF” and enter it.",
    docs: "Imou Open Platform (developer account required)",
  },
  tapo: {
    id: "tapo",
    label: "TP-Link Tapo",
    kinds: ["camera"],
    defaultHttpPort: 443,
    defaultRtspPort: 554,
    snapshotPath: null,
    channelDiscovery: null,
    video: "none",
    notes: "Tapo has no documented HTTP snapshot API and no official cloud API; its local API is proprietary (not reverse-engineered here). RTSP/ONVIF work only on the LAN. Registration and reachability only.",
    docs: "No documented public API",
  },
  smartlife: {
    id: "smartlife",
    label: "Smart Life / Tuya-based",
    kinds: ["camera"],
    defaultHttpPort: 80,
    defaultRtspPort: 554,
    snapshotPath: null,
    channelDiscovery: null,
    video: "none",
    notes: "Smart Life video is only available through Tuya's developer cloud (account required, excluded). Registration only; no video in the Free Edition.",
    docs: "Tuya Developer Platform (developer account required)",
  },
  generic: {
    id: "generic",
    label: "Other / ONVIF (custom snapshot URL)",
    kinds: ["camera", "nvr"],
    defaultHttpPort: 80,
    defaultRtspPort: 554,
    snapshotPath: null,
    channelDiscovery: null,
    video: "snapshot",
    notes: "Enter the HTTP snapshot path documented by your manufacturer (or the camera's ONVIF snapshot URI). Video is enabled only after a real JPEG is received.",
    docs: "Manufacturer documentation",
  },
};

export function brand(id: string): Brand {
  return BRANDS[id] ?? BRANDS.generic;
}

/** Snapshot path for a camera, or a human-readable reason why there is none. */
export function snapshotPathFor(b: Brand, channel: number, custom: string | null): { path: string | null; reason: string | null } {
  if (b.video === "none") return { path: null, reason: b.notes };
  if (custom) return { path: custom, reason: null };
  if (b.snapshotPath) return { path: b.snapshotPath(channel), reason: null };
  return { path: null, reason: "Enter this model's documented HTTP snapshot path to enable the snapshot view." };
}
