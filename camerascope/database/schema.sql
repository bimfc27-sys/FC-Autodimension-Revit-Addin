-- CameraScope PostgreSQL schema (generated from backend/app/models.py)
-- Reference only: the schema is created and upgraded by Alembic migrations (backend/migrations).

BEGIN;

CREATE TABLE provider_syncs (
	provider VARCHAR(40) NOT NULL, 
	last_run_at TIMESTAMP WITH TIME ZONE, 
	last_ok BOOLEAN NOT NULL, 
	imported INTEGER NOT NULL, 
	message TEXT, 
	PRIMARY KEY (provider)
);


CREATE TABLE public_cameras (
	id UUID NOT NULL, 
	provider VARCHAR(40) NOT NULL, 
	external_id VARCHAR(200) NOT NULL, 
	name VARCHAR(300) NOT NULL, 
	description TEXT, 
	camera_type VARCHAR(20) NOT NULL, 
	latitude FLOAT NOT NULL, 
	longitude FLOAT NOT NULL, 
	stream_type VARCHAR(20) NOT NULL, 
	stream_url TEXT, 
	snapshot_url TEXT, 
	page_url TEXT, 
	source_name VARCHAR(200) NOT NULL, 
	source_url TEXT, 
	license VARCHAR(200), 
	snapshot_allowed BOOLEAN NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	last_checked_at TIMESTAMP WITH TIME ZONE, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_public_camera_provider_ext UNIQUE (provider, external_id)
);

CREATE INDEX ix_public_cameras_lat_lon ON public_cameras (latitude, longitude);

CREATE TABLE users (
	id UUID NOT NULL, 
	email VARCHAR(320) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	display_name VARCHAR(120) NOT NULL, 
	is_admin BOOLEAN NOT NULL, 
	failed_logins INTEGER NOT NULL, 
	locked_until TIMESTAMP WITH TIME ZONE, 
	token_version INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_users_email ON users (email);

CREATE TABLE audit_log (
	id SERIAL NOT NULL, 
	user_id UUID, 
	action VARCHAR(60) NOT NULL, 
	target VARCHAR(200), 
	ip VARCHAR(64), 
	detail JSON NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_audit_log_user_id ON audit_log (user_id);

CREATE TABLE camera_groups (
	id UUID NOT NULL, 
	owner_id UUID NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	position INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_camera_groups_owner_id ON camera_groups (owner_id);

CREATE TABLE diagnostic_runs (
	id SERIAL NOT NULL, 
	user_id UUID NOT NULL, 
	camera_kind VARCHAR(10) NOT NULL, 
	camera_id UUID NOT NULL, 
	ok BOOLEAN NOT NULL, 
	steps JSON NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_diagnostic_runs_user_id ON diagnostic_runs (user_id);

CREATE TABLE favorites (
	user_id UUID NOT NULL, 
	camera_kind VARCHAR(10) NOT NULL, 
	camera_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (user_id, camera_kind, camera_id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);


CREATE TABLE gateways (
	id UUID NOT NULL, 
	owner_id UUID NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	description TEXT, 
	token_hash VARCHAR(64), 
	enrollment_code_hash VARCHAR(64), 
	enrollment_expires_at TIMESTAMP WITH TIME ZONE, 
	status VARCHAR(20) NOT NULL, 
	last_seen_at TIMESTAMP WITH TIME ZONE, 
	agent_version VARCHAR(40), 
	allowed_subnets JSON NOT NULL, 
	remote_address VARCHAR(64), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_gateways_owner_id ON gateways (owner_id);

CREATE TABLE sites (
	id UUID NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	address VARCHAR(300), 
	created_by UUID, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL
);


CREATE TABLE view_history (
	id SERIAL NOT NULL, 
	user_id UUID NOT NULL, 
	camera_kind VARCHAR(10) NOT NULL, 
	camera_id UUID NOT NULL, 
	viewed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_view_history_user_time ON view_history (user_id, viewed_at);

CREATE TABLE camera_group_items (
	group_id UUID NOT NULL, 
	camera_kind VARCHAR(10) NOT NULL, 
	camera_id UUID NOT NULL, 
	position INTEGER NOT NULL, 
	PRIMARY KEY (group_id, camera_kind, camera_id), 
	FOREIGN KEY(group_id) REFERENCES camera_groups (id) ON DELETE CASCADE
);


CREATE TABLE devices (
	id UUID NOT NULL, 
	owner_id UUID NOT NULL, 
	gateway_id UUID, 
	site_id UUID, 
	name VARCHAR(200) NOT NULL, 
	kind VARCHAR(10) NOT NULL, 
	protocol VARCHAR(20) NOT NULL, 
	vendor VARCHAR(40) NOT NULL, 
	host VARCHAR(255) NOT NULL, 
	port INTEGER, 
	rtsp_port INTEGER, 
	use_tls BOOLEAN NOT NULL, 
	credentials_ciphertext BYTEA, 
	credentials_nonce BYTEA, 
	credentials_key_id VARCHAR(40), 
	authorization_attested_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	authorization_basis VARCHAR(40) NOT NULL, 
	model VARCHAR(200), 
	status VARCHAR(20) NOT NULL, 
	last_error TEXT, 
	last_discovery_at TIMESTAMP WITH TIME ZONE, 
	auth_failures INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(gateway_id) REFERENCES gateways (id) ON DELETE RESTRICT, 
	FOREIGN KEY(site_id) REFERENCES sites (id) ON DELETE SET NULL
);

CREATE INDEX ix_devices_gateway_id ON devices (gateway_id);
CREATE INDEX ix_devices_owner_id ON devices (owner_id);

CREATE TABLE site_members (
	site_id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	added_by UUID, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (site_id, user_id), 
	FOREIGN KEY(site_id) REFERENCES sites (id) ON DELETE CASCADE, 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(added_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_site_members_user_id ON site_members (user_id);

CREATE TABLE private_cameras (
	id UUID NOT NULL, 
	owner_id UUID NOT NULL, 
	gateway_id UUID, 
	device_id UUID, 
	site_id UUID, 
	name VARCHAR(200) NOT NULL, 
	description TEXT, 
	protocol VARCHAR(20) NOT NULL, 
	vendor VARCHAR(40) NOT NULL, 
	host VARCHAR(255) NOT NULL, 
	port INTEGER, 
	use_tls BOOLEAN NOT NULL, 
	config JSON NOT NULL, 
	latitude FLOAT, 
	longitude FLOAT, 
	credentials_ciphertext BYTEA, 
	credentials_nonce BYTEA, 
	credentials_key_id VARCHAR(40), 
	authorization_attested_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	authorization_basis VARCHAR(40) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	last_error TEXT, 
	last_checked_at TIMESTAMP WITH TIME ZONE, 
	auth_failures INTEGER NOT NULL, 
	reachable BOOLEAN, 
	reachable_checked_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(gateway_id) REFERENCES gateways (id) ON DELETE RESTRICT, 
	FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE CASCADE, 
	FOREIGN KEY(site_id) REFERENCES sites (id) ON DELETE SET NULL
);

CREATE INDEX ix_private_cameras_device_id ON private_cameras (device_id);
CREATE INDEX ix_private_cameras_gateway_id ON private_cameras (gateway_id);
CREATE INDEX ix_private_cameras_owner_id ON private_cameras (owner_id);
CREATE INDEX ix_private_cameras_site_id ON private_cameras (site_id);

COMMIT;
