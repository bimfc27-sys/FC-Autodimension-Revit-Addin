export type CameraKind = "public" | "private";

export interface User {
  id: string;
  email: string;
  display_name: string;
  is_admin: boolean;
}

export interface PublicCamera {
  kind: "public";
  id: string;
  provider: string;
  name: string;
  description: string | null;
  camera_type: "traffic" | "webcam" | "public_ip";
  latitude: number;
  longitude: number;
  stream_type: "hls" | "mjpeg" | "image" | "webpage" | "gateway";
  page_url: string | null;
  source_name: string;
  source_url: string | null;
  license: string | null;
  snapshot_allowed: boolean;
  status: string;
  last_checked_at: string | null;
  distance_km: number | null;
  saved: boolean;
}

export type Protocol = "rtsp" | "onvif" | "webrtc" | "hls" | "mjpeg" | "vendor_api";
export type Vendor = "generic" | "axis" | "hikvision" | "dahua" | "amcrest" | "reolink";

export interface CameraConfig {
  main_path?: string | null;
  sub_path?: string | null;
  snapshot_path?: string | null;
  rtsp_transport?: "automatic" | "tcp" | "udp";
  channel?: number;
  onvif_main_profile?: string | null;
  onvif_sub_profile?: string | null;
  resolved_main_uri?: string | null;
  resolved_sub_uri?: string | null;
  resolved_snapshot_uri?: string | null;
  snapshot_allowed?: boolean;
}

export interface PrivateCamera {
  kind: "private";
  id: string;
  name: string;
  description: string | null;
  protocol: Protocol;
  vendor: Vendor;
  host: string;
  port: number | null;
  use_tls: boolean;
  config: CameraConfig;
  latitude: number | null;
  longitude: number | null;
  has_credentials: boolean;
  username: string | null;
  authorization_basis: "owner" | "authorized_user";
  authorization_attested_at: string;
  status: string;
  last_error: string | null;
  last_checked_at: string | null;
  created_at: string;
}

export type AnyCamera = PublicCamera | PrivateCamera;

export interface Playback {
  kind: CameraKind;
  camera_id: string;
  mode: "gateway" | "hls" | "mjpeg" | "image" | "webpage";
  hls_url: string | null;
  whep_url: string | null;
  image_url: string | null;
  page_url: string | null;
  refresh_seconds: number | null;
  quality: string;
  qualities: { id: string; label: string }[];
  snapshot_allowed: boolean;
  snapshot_url: string | null;
  attribution: string | null;
}

export interface DiagnosticStep {
  name: string;
  ok: boolean;
  detail: string;
  duration_ms: number;
}

export interface DiagnosticsRun {
  id: number | null;
  camera_kind: CameraKind;
  camera_id: string;
  ok: boolean;
  steps: DiagnosticStep[];
  created_at: string | null;
}

export interface OnvifProfile {
  token: string;
  name: string;
  encoding: string | null;
  width: number | null;
  height: number | null;
  stream_uri: string | null;
  snapshot_uri: string | null;
}
