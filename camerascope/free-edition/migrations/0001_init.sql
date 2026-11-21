-- CameraScope Free Edition schema (Cloudflare D1 / SQLite).
-- Timestamps are Unix epoch seconds. IDs are UUID strings.

CREATE TABLE users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,          -- pbkdf2-sha256$<iterations>$<salt b64>$<hash b64>
  display_name TEXT NOT NULL,
  is_admin INTEGER NOT NULL DEFAULT 0,
  failed_logins INTEGER NOT NULL DEFAULT 0,
  locked_until INTEGER,
  created_at INTEGER NOT NULL
);

-- Server-side sessions: only the SHA-256 of the random token is stored.
CREATE TABLE sessions (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  last_seen_at INTEGER NOT NULL,
  ip TEXT,
  user_agent TEXT
);
CREATE INDEX ix_sessions_user ON sessions(user_id);

CREATE TABLE sites (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  address TEXT,
  created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at INTEGER NOT NULL
);

CREATE TABLE site_members (
  site_id TEXT NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('manager', 'viewer')),
  created_at INTEGER NOT NULL,
  PRIMARY KEY (site_id, user_id)
);

-- DVR / NVR recorders. Their channels are rows in `cameras` with device_id set.
CREATE TABLE devices (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  site_id TEXT REFERENCES sites(id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  brand TEXT NOT NULL,
  model TEXT,
  serial TEXT,
  host TEXT NOT NULL,
  http_port INTEGER NOT NULL,
  use_tls INTEGER NOT NULL DEFAULT 0,
  creds_ct TEXT, creds_iv TEXT, creds_key_id TEXT,   -- AES-256-GCM, AAD = id + owner
  authorization_basis TEXT NOT NULL,
  attested_at INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'unknown',
  status_reason TEXT,
  status_checked_at INTEGER,
  auth_failures INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);
CREATE INDEX ix_devices_owner ON devices(owner_id);

CREATE TABLE cameras (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  device_id TEXT REFERENCES devices(id) ON DELETE CASCADE,
  site_id TEXT REFERENCES sites(id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  brand TEXT NOT NULL,
  model TEXT,
  serial TEXT,
  notes TEXT,
  host TEXT NOT NULL,
  http_port INTEGER NOT NULL,
  use_tls INTEGER NOT NULL DEFAULT 0,
  rtsp_port INTEGER,
  channel INTEGER NOT NULL DEFAULT 1,
  snapshot_path TEXT,                  -- custom path; NULL = brand's documented default
  refresh_seconds INTEGER NOT NULL DEFAULT 3,
  creds_ct TEXT, creds_iv TEXT, creds_key_id TEXT,
  authorization_basis TEXT NOT NULL,
  attested_at INTEGER NOT NULL,
  latitude REAL, longitude REAL,
  status TEXT NOT NULL DEFAULT 'unknown',   -- online | offline | auth_failed | not_internet_reachable | unknown
  status_reason TEXT,
  status_checked_at INTEGER,
  video_mode TEXT NOT NULL DEFAULT 'none',  -- snapshot | none ; set ONLY by a successful verification
  video_reason TEXT,
  video_verified_at INTEGER,
  auth_failures INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX ix_cameras_owner ON cameras(owner_id);
CREATE INDEX ix_cameras_site ON cameras(site_id);
CREATE INDEX ix_cameras_device ON cameras(device_id);

CREATE TABLE camera_groups (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);

CREATE TABLE camera_group_items (
  group_id TEXT NOT NULL REFERENCES camera_groups(id) ON DELETE CASCADE,
  camera_kind TEXT NOT NULL,
  camera_id TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (group_id, camera_kind, camera_id)
);

CREATE TABLE favorites (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  camera_kind TEXT NOT NULL,
  camera_id TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (user_id, camera_kind, camera_id)
);

CREATE TABLE view_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  camera_kind TEXT NOT NULL,
  camera_id TEXT NOT NULL,
  viewed_at INTEGER NOT NULL
);
CREATE INDEX ix_history_user ON view_history(user_id, viewed_at);

CREATE TABLE diagnostic_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  camera_kind TEXT NOT NULL,
  camera_id TEXT NOT NULL,
  ok INTEGER NOT NULL,
  steps TEXT NOT NULL,                 -- JSON
  created_at INTEGER NOT NULL
);
CREATE INDEX ix_diag_user ON diagnostic_runs(user_id, created_at);

CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT,
  action TEXT NOT NULL,
  target TEXT,
  ip TEXT,
  detail TEXT,                         -- JSON
  created_at INTEGER NOT NULL
);
CREATE INDEX ix_audit_time ON audit_log(created_at);

-- Fixed-window counters for login / registration / diagnostics rate limits.
CREATE TABLE rate_limits (
  key TEXT PRIMARY KEY,
  window_start INTEGER NOT NULL,
  count INTEGER NOT NULL
);

CREATE TABLE public_cameras (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  external_id TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT,
  camera_type TEXT NOT NULL,
  latitude REAL NOT NULL,
  longitude REAL NOT NULL,
  stream_type TEXT NOT NULL,
  stream_url TEXT,
  snapshot_url TEXT,
  page_url TEXT,
  source_name TEXT NOT NULL,
  source_url TEXT,
  license TEXT,
  snapshot_allowed INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'unknown',
  updated_at INTEGER NOT NULL,
  UNIQUE (provider, external_id)
);
CREATE INDEX ix_public_latlon ON public_cameras(latitude, longitude);
