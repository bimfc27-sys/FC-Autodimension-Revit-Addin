"""Database models.

Public and private cameras live in SEPARATE tables on purpose: a public feed can
never accidentally inherit an owner's credentials, and every private-camera
query is forced to filter on ``owner_id``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token_version: Mapped[int] = mapped_column(Integer, default=0)  # bump to revoke all sessions
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PublicCamera(Base):
    """A camera whose operator intentionally publishes the feed to the public."""

    __tablename__ = "public_cameras"
    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_public_camera_provider_ext"),
        Index("ix_public_cameras_lat_lon", "latitude", "longitude"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    # traffic | webcam | public_ip
    camera_type: Mapped[str] = mapped_column(String(20))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    # hls | mjpeg | image | webpage
    stream_type: Mapped[str] = mapped_column(String(20))
    stream_url: Mapped[str | None] = mapped_column(Text)
    snapshot_url: Mapped[str | None] = mapped_column(Text)
    page_url: Mapped[str | None] = mapped_column(Text)
    source_name: Mapped[str] = mapped_column(String(200))
    source_url: Mapped[str | None] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(String(200))
    snapshot_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    # online | offline | unknown
    status: Mapped[str] = mapped_column(String(20), default="unknown")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PrivateCamera(Base):
    """A camera the owning user has attested they own or are authorised to view."""

    __tablename__ = "private_cameras"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # NULL = reached directly by the cloud gateway (public address or VPN route).
    gateway_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("gateways.id", ondelete="RESTRICT"), index=True)
    # Set for channels imported from a DVR/NVR; credentials are then inherited from the device.
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    # rtsp | onvif | webrtc | hls | mjpeg | vendor_api
    protocol: Mapped[str] = mapped_column(String(20))
    # generic | axis | hikvision | dahua | reolink | amcrest
    vendor: Mapped[str] = mapped_column(String(40), default="generic")
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int | None] = mapped_column(Integer)
    use_tls: Mapped[bool] = mapped_column(Boolean, default=False)
    # Non secret connection settings: stream paths, rtsp transport, onvif profile tokens...
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)

    # Encrypted {"username":..., "password":...}. AES-256-GCM, AAD bound to row+owner.
    credentials_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    credentials_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    credentials_key_id: Mapped[str | None] = mapped_column(String(40))

    authorization_attested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    authorization_basis: Mapped[str] = mapped_column(String(40))  # owner | authorized_user

    status: Mapped[str] = mapped_column(String(20), default="unknown")  # online|offline|auth_failed|unknown
    last_error: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Consecutive authentication failures. The camera is locked after a few so the
    # platform can never be used as a password oracle against a device.
    auth_failures: Mapped[int] = mapped_column(Integer, default=0)
    # Unauthenticated TCP reachability, measured from wherever the camera is reached
    # (its gateway, or the cloud). Never inferred from location.
    reachable: Mapped[bool | None] = mapped_column(Boolean)
    reachable_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Gateway(Base):
    """A Local Camera Gateway running inside a network the owner is authorised to use.

    It connects OUTBOUND to the cloud over an authenticated, TLS-encrypted WebSocket
    tunnel, so no inbound ports are opened on the camera network.
    """

    __tablename__ = "gateways"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    # sha256 of the long-lived gateway secret (the secret itself is shown once)
    token_hash: Mapped[str | None] = mapped_column(String(64))
    enrollment_code_hash: Mapped[str | None] = mapped_column(String(64))
    enrollment_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | online | offline
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agent_version: Mapped[str | None] = mapped_column(String(40))
    # Reported by the agent from its local configuration (authoritative on the gateway side)
    allowed_subnets: Mapped[list] = mapped_column(JSON, default=list)
    remote_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Device(Base):
    """A DVR / NVR recording device whose channels are exposed as cameras."""

    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    gateway_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("gateways.id", ondelete="RESTRICT"), index=True)
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(10), default="nvr")  # nvr | dvr
    protocol: Mapped[str] = mapped_column(String(20))  # onvif | vendor_api
    vendor: Mapped[str] = mapped_column(String(40), default="generic")
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int | None] = mapped_column(Integer)
    rtsp_port: Mapped[int | None] = mapped_column(Integer)
    use_tls: Mapped[bool] = mapped_column(Boolean, default=False)
    credentials_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    credentials_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    credentials_key_id: Mapped[str | None] = mapped_column(String(40))
    authorization_attested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    authorization_basis: Mapped[str] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="unknown")
    last_error: Mapped[str | None] = mapped_column(Text)
    last_discovery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auth_failures: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Site(Base):
    """A building / location whose cameras are shared with its members."""

    __tablename__ = "sites"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SiteMember(Base):
    __tablename__ = "site_members"

    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    # manager: manage members and assign own cameras | viewer: view shared cameras only
    role: Mapped[str] = mapped_column(String(20))
    added_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CameraGroup(Base):
    __tablename__ = "camera_groups"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CameraGroupItem(Base):
    __tablename__ = "camera_group_items"

    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("camera_groups.id", ondelete="CASCADE"), primary_key=True)
    camera_kind: Mapped[str] = mapped_column(String(10), primary_key=True)
    camera_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    position: Mapped[int] = mapped_column(Integer, default=0)


class Favorite(Base):
    __tablename__ = "favorites"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    camera_kind: Mapped[str] = mapped_column(String(10), primary_key=True)  # public | private
    camera_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ViewHistory(Base):
    __tablename__ = "view_history"
    __table_args__ = (Index("ix_view_history_user_time", "user_id", "viewed_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    camera_kind: Mapped[str] = mapped_column(String(10))  # public | private
    camera_id: Mapped[uuid.UUID] = mapped_column()
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DiagnosticRun(Base):
    __tablename__ = "diagnostic_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    camera_kind: Mapped[str] = mapped_column(String(10))
    camera_id: Mapped[uuid.UUID] = mapped_column()
    ok: Mapped[bool] = mapped_column(Boolean)
    steps: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(60))
    target: Mapped[str | None] = mapped_column(String(200))
    ip: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProviderSync(Base):
    __tablename__ = "provider_syncs"

    provider: Mapped[str] = mapped_column(String(40), primary_key=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    imported: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text)
