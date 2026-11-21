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

export type Protocol = "rtsp" | "onvif" | "webrtc" | "hls" | "mjpeg" | "vendor_api" | "snapshot";
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

export type CameraStatus = "online" | "offline" | "auth_failed" | "gateway_offline" | "not_internet_reachable" | "unknown";

export interface Ref {
  id: string;
  name: string;
}

export interface GatewayRef extends Ref {
  online: boolean;
}

export interface PrivateCamera {
  kind: "private";
  id: string;
  name: string;
  description: string | null;
  protocol: Protocol;
  vendor: Vendor;
  is_owner: boolean;
  access: "owner" | "shared";
  host: string | null;
  port: number | null;
  use_tls: boolean;
  config: CameraConfig;
  latitude: number | null;
  longitude: number | null;
  has_credentials: boolean;
  username: string | null;
  authorization_basis: "owner" | "authorized_user";
  authorization_attested_at: string;
  status: CameraStatus;
  connection: "gateway" | "cloud";
  gateway: GatewayRef | null;
  device: Ref | null;
  site: Ref | null;
  channel: number | null;
  reachable_checked_at: string | null;
  last_error: string | null;
  last_checked_at: string | null;
  created_at: string;
  /** Free Edition: whether (and how) video is available, with the reason when it is not */
  video?: { available: boolean; mode: "snapshot" | "none"; refresh_seconds: number; verified_at: string | null; reason: string | null };
  brand_label?: string;
  model?: string | null;
  serial?: string | null;
}

export interface Gateway {
  id: string;
  name: string;
  description: string | null;
  status: "pending" | "online" | "offline";
  online: boolean;
  last_seen_at: string | null;
  agent_version: string | null;
  allowed_subnets: string[];
  remote_address: string | null;
  camera_count: number;
  device_count: number;
  created_at: string;
}

export interface Enrollment {
  gateway: Gateway;
  enrollment_code: string;
  expires_at: string;
}

export interface Device {
  id: string;
  name: string;
  kind: "nvr" | "dvr";
  protocol: "onvif" | "vendor_api";
  vendor: string;
  host: string;
  port: number | null;
  rtsp_port: number | null;
  use_tls: boolean;
  model: string | null;
  status: string;
  last_error: string | null;
  last_discovery_at: string | null;
  connection: "gateway" | "cloud";
  gateway: GatewayRef | null;
  site: Ref | null;
  channel_count: number;
}

export interface DeviceChannel {
  index: number;
  name: string;
  main_profile: string | null;
  sub_profile: string | null;
  resolution: string | null;
  has_sub_stream: boolean;
  imported_camera_id: string | null;
}

export interface Site {
  id: string;
  name: string;
  address: string | null;
  role: "manager" | "viewer";
  camera_count: number;
  member_count: number;
}

export interface SiteMember {
  user_id: string;
  email: string;
  display_name: string;
  role: "manager" | "viewer";
  added_at: string;
}

export interface CameraGroup {
  id: string;
  name: string;
  position: number;
  cameras: AnyCamera[];
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
  source_token: string | null;
  encoding: string | null;
  width: number | null;
  height: number | null;
  has_stream: boolean;
}
